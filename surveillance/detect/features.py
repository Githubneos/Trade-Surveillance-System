"""Feature engineering for the per-trade scorer.

Two rules govern everything in this module, and both exist to stop the scorer cheating.

**1. Features are account-relative, never absolute.**
A $2m trade is an outlier for a retail account and a Tuesday for a pension fund. Every
size and timing feature is expressed against the placing account's own history, which is
also what makes the "legitimate institutional block" hard negative survivable.

**2. Features are causal.**
A trade's own row never contributes to its own baseline, and no future trade does either.
Baselines are trailing expanding statistics, shifted by one. This matters more than it
looks: a mean that includes the trade being scored is dragged toward it, which *shrinks*
the apparent anomaly and quietly destroys recall on exactly the cases that matter. The
opposite mistake -- a baseline computed over the whole window -- leaks the future and
inflates every number instead.

Nothing here reads ground truth. Baselines are computed over **all** trades, because a
live system does not know which trades are planted. (The evaluation-side helper in
``eval/dataset_report.py`` deliberately does the opposite, filtering to known-clean rows;
reusing it here would be label leakage.)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Trades an account needs before its own history means anything. Below this the
#: account-relative features are undefined and are reported as such rather than guessed.
MIN_HISTORY = 15

#: Width of the intraday histogram buckets used for the time-of-day feature. 30 minutes
#: over a 390-minute session gives 13 buckets: fine enough to separate the open from the
#: close, coarse enough that a moderately active account fills them in a few days.
TIME_BUCKET_MINUTES = 30
N_TIME_BUCKETS = 13

#: Trailing trades in the same security used as the reference price.
PRICE_REF_TRADES = 41

#: Features handed to the per-trade model, in a fixed order so a persisted model and a
#: fresh frame can never silently disagree about which column is which.
#:
#: Every one answers the same question -- "how far is this trade from what this account
#: normally does" -- on a continuous, roughly symmetric scale. That coherence is the point:
#: a scale-based scorer compares features against each other, so mixing a deviation
#: magnitude with a membership flag lets the flag win every comparison for reasons
#: unrelated to anomalousness.
FEATURE_COLUMNS = (
    "z_log_notional",
    "qty_ratio_to_median",
    "price_dev_z",
    "time_of_day_surprise",
    "gap_ratio",
    "burst_ratio",
)

#: Computed and carried alongside, but deliberately NOT scored per-trade.
#:
#: These describe an account's *relationship* to a security or to a direction, not the
#: unusualness of one execution. security_novelty in particular is near-binary in practice:
#: scored per-trade it simply flags every account's first trade in any name, which is
#: thousands of entirely ordinary trades. Both are genuinely informative -- as gates in the
#: graph layer, where "no prior history in this name" is exactly the right question to ask
#: about a coordinated cluster.
CONTEXT_COLUMNS = (
    "security_novelty",
    "side_imbalance_20",
)


def _expanding_causal(g: pd.Series, fn: str) -> pd.Series:
    """Expanding statistic over *strictly prior* rows."""
    return getattr(g.shift(1).expanding(min_periods=MIN_HISTORY), fn)()


def build_features(trades: pd.DataFrame) -> pd.DataFrame:
    """Return one feature row per trade, in the input's original row order.

    ``trades`` needs external_id, account_id, security_id, side, quantity, price and
    executed_at. Extra columns are ignored, which keeps this callable with either a
    database frame or the generator's parquet.
    """
    df = trades.copy()
    df["executed_at"] = pd.to_datetime(df["executed_at"], utc=True)
    df["notional"] = df["quantity"].astype(float) * df["price"].astype(float)
    df["log_notional"] = np.log(df["notional"].clip(lower=1e-9))
    original_order = df.index.copy()
    # external_id breaks ties. Trades sharing an account and a timestamp are common (a
    # parent order filling in slices), and without a deterministic tiebreak their relative
    # order -- and therefore every expanding statistic downstream -- depends on the order
    # rows happened to arrive in. That makes results irreproducible in a way no metric
    # would reveal.
    df = df.sort_values(["account_id", "executed_at", "external_id"], kind="stable")

    acct = df.groupby("account_id", sort=False)

    # --- size, relative to the account's own trailing history ---------------------------
    mu = acct["log_notional"].transform(lambda g: _expanding_causal(g, "mean"))
    sd = acct["log_notional"].transform(lambda g: _expanding_causal(g, "std"))
    # A degenerate sd (an account that always trades the same size) would otherwise produce
    # infinite z-scores on the first trade that differs at all.
    sd = sd.where(sd > 1e-6, np.nan)
    df["z_log_notional"] = (df["log_notional"] - mu) / sd

    med = acct["quantity"].transform(
        lambda g: g.shift(1).expanding(min_periods=MIN_HISTORY).median()
    )
    df["qty_ratio_to_median"] = np.log(
        df["quantity"].astype(float).clip(lower=1e-9) / med.clip(lower=1e-9)
    )

    # --- price, relative to where the security was actually trading ---------------------
    # The reference is a centred rolling median of nearby trades in the same security. The
    # generator's true mid is NOT used: a real system only sees the tape.
    #
    # The deviation is then divided by that security's OWN typical deviation. A 40bps move
    # is nothing in a thin name and a screaming outlier in a mega-cap, so raw basis points
    # would simply rank every illiquid security above every liquid one.
    df = df.sort_values(["security_id", "executed_at", "external_id"], kind="stable")
    sec = df.groupby("security_id", sort=False)["price"]
    # TRAILING, not centred. A centred window is the natural way to write a "prevailing
    # price" and it reads twenty trades from the future -- which a live scorer does not
    # have, and which inflates every price-outlier metric. Measured, the centred version
    # made 95% of this feature's values future-dependent.
    #
    # The cost of going causal is real and worth naming: after a genuine price move the
    # trailing median lags, so the first trades at the new level look deviant. That is a
    # false-positive source a real system manages with a short trailing VWAP or the
    # prevailing quote, neither of which exists in execution-only data.
    ref = sec.transform(
        lambda g: g.shift(1).rolling(PRICE_REF_TRADES, min_periods=5).median()
    )
    dev_bps = (df["price"].astype(float) / ref - 1.0) * 1e4
    df["_dev_bps"] = dev_bps
    # The security's own typical deviation, also from strictly prior trades.
    typical = df.groupby("security_id", sort=False)["_dev_bps"].transform(
        lambda g: g.abs().shift(1).expanding(min_periods=MIN_HISTORY).median()
    )
    df["price_dev_z"] = dev_bps / typical.clip(lower=0.5)

    # --- timing, relative to the account's own intraday habit ---------------------------
    df = df.sort_values(["account_id", "executed_at", "external_id"], kind="stable")
    minutes = (
        df["executed_at"].dt.tz_convert("America/New_York").dt.hour * 60
        + df["executed_at"].dt.tz_convert("America/New_York").dt.minute
    ).astype(float)
    df["_minute"] = minutes
    # Surprise is measured against the account's own EMPIRICAL intraday histogram, not a
    # mean and standard deviation. Trading intensity is U-shaped -- heavy at the open and
    # into the close, thin at midday -- so a Gaussian z-score reports a normal lunchtime
    # trade as unremarkable (it sits near the mean) and a normal closing trade as extreme
    # (it sits in the tail). That is backwards, and it is why a z-score on this feature
    # detects nothing: the distribution it assumes does not exist.
    #
    # Counts are cumulative over strictly prior trades, so the measure stays causal, and
    # Laplace-smoothed so an account's first visit to a bucket is surprising but not
    # infinitely so.
    df["_bucket"] = (df["_minute"] // TIME_BUCKET_MINUTES).astype(int)
    prior_in_bucket = df.groupby(["account_id", "_bucket"], sort=False).cumcount()
    prior_total = df.groupby("account_id", sort=False).cumcount()
    alpha = 0.5
    prob = (prior_in_bucket + alpha) / (prior_total + alpha * N_TIME_BUCKETS)
    df["time_of_day_surprise"] = -np.log(prob)
    df.loc[prior_total < MIN_HISTORY, "time_of_day_surprise"] = np.nan

    # --- rhythm: gaps and bursts, both relative to the account's own tempo ---------------
    # An absolute burst count would rank every market maker above every retail account
    # permanently: 60 trades in ten minutes is a quiet morning for one and unprecedented
    # for the other. What matters is a departure from an account's OWN rhythm.
    gap = acct["executed_at"].transform(lambda g: g.diff().dt.total_seconds())
    log_gap = np.log1p(gap.clip(lower=0))
    df["_log_gap"] = log_gap
    acct = df.groupby("account_id", sort=False)
    typical_gap = acct["_log_gap"].transform(lambda g: _expanding_causal(g, "median"))
    df["gap_ratio"] = log_gap - typical_gap

    burst = (
        acct.apply(lambda g: _rolling_count(g, "10min"), include_groups=False)
        .reset_index(level=0, drop=True)
    )
    df["_burst"] = burst
    acct = df.groupby("account_id", sort=False)
    typical_burst = acct["_burst"].transform(lambda g: _expanding_causal(g, "median"))
    df["burst_ratio"] = (burst + 1.0) / (typical_burst.fillna(0.0) + 1.0)

    # --- behavioural novelty ------------------------------------------------------------
    # Has this account traded this name before? Manipulation often shows up in a security
    # the account has no history in.
    # Decays smoothly rather than as 1/(1+n): the reciprocal form piles almost all its mass
    # on a handful of discrete values, which collapses the feature's spread and lets it
    # dominate any scale-based scorer for reasons that have nothing to do with anomaly.
    seen_before = (
        df.groupby(["account_id", "security_id"], sort=False).cumcount().astype(float)
    )
    df["security_novelty"] = np.exp(-seen_before / 5.0)

    buy = (df["side"] == "buy").astype(float)
    df["side_imbalance_20"] = (
        acct["side"]
        .transform(lambda g: (g == "buy").astype(float).rolling(20, min_periods=5).mean())
        .sub(0.5)
        .abs()
    )
    del buy

    out = df.loc[original_order, ["external_id", "account_id", "security_id", "executed_at"]]
    for col in (*FEATURE_COLUMNS, *CONTEXT_COLUMNS):
        out[col] = df.loc[original_order, col]
    out["has_history"] = df.loc[original_order, "z_log_notional"].notna()
    return out


def _rolling_count(group: pd.DataFrame, window: str) -> pd.Series:
    """Trades by this account in the trailing window, excluding the trade itself."""
    s = pd.Series(1.0, index=pd.DatetimeIndex(group["executed_at"]))
    counted = s.rolling(window).sum().to_numpy() - 1.0
    return pd.Series(counted, index=group.index)


def impute(features: pd.DataFrame) -> pd.DataFrame:
    """Fill undefined features with neutral values for the model.

    Accounts below MIN_HISTORY have no meaningful baseline. Imputing to the neutral value
    (zero deviation) is the conservative choice: it makes them look ordinary rather than
    anomalous, so a brand-new account cannot be flagged purely for being new. That costs
    some recall on early trades and is the right trade in a compliance setting, where a
    false accusation is more expensive than a late one.
    """
    out = features.copy()
    neutral = {
        "z_log_notional": 0.0,
        "qty_ratio_to_median": 0.0,
        "price_dev_z": 0.0,
        "time_of_day_surprise": 0.0,
        "gap_ratio": 0.0,
        "burst_ratio": 1.0,
        "security_novelty": 0.0,
        "side_imbalance_20": 0.0,
    }
    for col, value in neutral.items():
        out[col] = out[col].astype(float).replace([np.inf, -np.inf], np.nan).fillna(value)
    return out
