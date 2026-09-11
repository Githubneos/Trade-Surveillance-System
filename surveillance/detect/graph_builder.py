"""Inferred trading-relationship graphs.

The detector is never told who traded with whom. ``trades.counterparty_account_id`` exists
for ground truth and display, and an AST test forbids this package from reading it, because
a lit venue does not publish counterparty identity and a detector handed it would be
solving a different, much easier problem.

So relationships are **inferred** from co-trading: two accounts that repeatedly transact
the same security within a short window are probably transacting with each other.

## Two graphs, not one

A feasibility probe (`docs/graph-layer-feasibility.md`) established that one graph cannot
serve both typologies:

* **Wash rings** are reciprocal -- A sells to B and B sells back to A. They need *directed,
  opposite-side* edges, and they are found by reciprocity (measured 0.81-1.00 within rings
  against ~0.49 for ordinary pairs).
* **Coordinated clusters** are directionally unanimous -- everyone buys. They produce
  **zero** opposite-side edges, so the ring graph cannot see them at all. They need
  *undirected, same-side* edges.

Building one graph with two thresholds would have caught rings, scored zero on clusters,
and buried that inside an aggregate recall number.

## Why raw co-trade counts are useless

Also from the probe: within a window of n trades, a security generates O(n^2) candidate
pairs, and the pairs with the highest raw counts are simply the pairs of busiest accounts.
Ring members scored *below* random pairs (0.2x-1.7x). Every edge weight here is therefore
normalised against what the pair would be expected to do by chance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Co-trading window by liquidity tier, in seconds. A thin name trades so rarely that two
#: trades minutes apart are plausibly the same event; a mega-cap prints continuously, so
#: only near-simultaneous trades carry any information about a relationship.
WINDOW_BY_TIER = {"illiquid": 180, "mid": 90, "liquid": 30}
DEFAULT_WINDOW_S = 90

#: The same-side graph uses a much wider window, because the two typologies operate on
#: different timescales and a single window cannot serve both.
#:
#: A wash ring passes a position around in seconds to minutes -- a wide window there would
#: swamp the reciprocal signal with unrelated flow. A coordinated cluster unfolds over
#: twenty to forty-five minutes, and at a three-minute window only the handful of
#: participants who happened to trade near-simultaneously form edges at all: measured, that
#: recovered five members of a fifteen-account cluster, too few to clear the evaluation
#: contract's overlap threshold even though the cluster had plainly been found.
#: Scaled by liquidity for the same reason the ring window is. A flat 30-minute window
#: sounds right for a typology that unfolds over 20-45 minutes, and applied to a mega-cap
#: it makes every pair of active accounts a co-trading "relationship": measured, it tripled
#: the pair count and fired all three liquid-crowding hard negatives. Thin names get the
#: wide window the typology needs; liquid names, where half an hour is thousands of prints,
#: do not.
CLUSTER_WINDOW_BY_TIER = {"illiquid": 1800, "mid": 600, "liquid": 120}


PAIR_COLUMNS = (
    "a",
    "b",
    "security_id",
    "a_sold_to_b",
    "b_sold_to_a",
    "same_side",
    "expected_opposite",
    "expected_same",
    "first_seen",
    "last_seen",
)


def _session_aware_expectation(
    n_a: np.ndarray, n_b: np.ndarray, window_s: float, active_span_s: float
) -> np.ndarray:
    """Expected co-trades for a pair under independent trading.

    The naive version divides by the full multi-day span, ignoring that trading only
    happens during sessions and is U-shaped within them. That overstates the denominator so
    badly that every scenario, abusive or not, collapses to a lift of about 0.8 -- which is
    exactly what the feasibility probe measured, and why it concluded a flat null model is
    useless here.

    Using the security's **actually active** span -- the time its own trades actually cover
    -- absorbs the overnight gaps and the intraday intensity profile without modelling
    either explicitly. Within that span two independent accounts placing n_a and n_b trades
    collide within +/-window at roughly (2 * window / span) per trade pair.
    """
    if active_span_s <= 0:
        return np.zeros_like(n_a, dtype=float)
    collision_rate = min(2.0 * window_s / active_span_s, 1.0)
    return n_a.astype(float) * n_b.astype(float) * collision_rate


def build_graphs(
    trades: pd.DataFrame, liquidity: dict[int, str] | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build both graphs: (reciprocal, same_side).

    Returned separately rather than as one frame with two weight columns because they are
    genuinely different graphs over the same accounts -- different windows, different edge
    semantics, different null expectations.
    """
    ring = build_pair_graph(trades, liquidity)
    cluster = build_pair_graph(trades, liquidity, window_by_tier=CLUSTER_WINDOW_BY_TIER)
    return ring, cluster


def build_pair_graph(
    trades: pd.DataFrame,
    liquidity: dict[int, str] | None = None,
    *,
    window_s: int | None = None,
    window_by_tier: dict[str, int] | None = None,
) -> pd.DataFrame:
    """Infer account-pair relationships from co-trading within each security.

    Returns one row per (account_a, account_b, security) observed together, with directed
    opposite-side counts, same-side counts, and the counts expected by chance.

    Vectorised deliberately: the readable nested-loop version took 148 seconds on this
    dataset, which is slow enough that nobody sweeps a window parameter, and an unswept
    parameter is an unjustified one.
    """
    cols = ["account_id", "security_id", "side", "executed_at"]
    df = trades[cols].copy()
    df["executed_at"] = pd.to_datetime(df["executed_at"], utc=True)
    df = df.sort_values(["security_id", "executed_at"], kind="stable")

    frames: list[pd.DataFrame] = []

    for security_id, g in df.groupby("security_id", sort=False):
        tier = (liquidity or {}).get(security_id, "mid")
        tiers = window_by_tier or WINDOW_BY_TIER
        w = window_s if window_s is not None else tiers.get(tier, DEFAULT_WINDOW_S)

        # Resolution-safe epoch seconds. `astype("int64") // 10**9` assumes nanoseconds;
        # this dataset round-trips through parquet as datetime64[ms], so that assumption
        # silently produced 1741 instead of 1741012291, collapsed every security's span to
        # about two seconds, and made every trade a neighbour of every other. The graph
        # still looked healthy -- lift came out at a tidy 1.0 everywhere, because the null
        # model was correctly calibrated against nonsense.
        ts = (
            g["executed_at"].dt.tz_convert("UTC").dt.tz_localize(None)
            .astype("datetime64[s]").astype("int64").to_numpy()
        )
        acct = g["account_id"].to_numpy()
        buy = (g["side"].to_numpy() == "buy")
        n = len(ts)
        if n < 2:
            continue

        # For each trade i, the half-open range of trades within +w seconds after it.
        hi = np.searchsorted(ts, ts + w, side="right")
        widths = hi - np.arange(n) - 1
        widths[widths < 0] = 0
        if widths.sum() == 0:
            continue

        i_idx = np.repeat(np.arange(n), widths)
        # Offsets 1..width for each i, flattened.
        starts = np.repeat(np.arange(n) + 1, widths)
        ranks = np.arange(widths.sum()) - np.repeat(np.cumsum(widths) - widths, widths)
        j_idx = starts + ranks

        a_raw, b_raw = acct[i_idx], acct[j_idx]
        keep = a_raw != b_raw
        if not keep.any():
            continue
        i_idx, j_idx = i_idx[keep], j_idx[keep]
        a_raw, b_raw = a_raw[keep], b_raw[keep]
        buy_i, buy_j = buy[i_idx], buy[j_idx]

        lo = np.minimum(a_raw, b_raw)
        hi_acct = np.maximum(a_raw, b_raw)
        opposite = buy_i != buy_j
        # Seller is whichever side was not the buyer.
        seller = np.where(buy_i, b_raw, a_raw)
        a_to_b = opposite & (seller == lo)
        b_to_a = opposite & (seller == hi_acct)

        pairs = pd.DataFrame(
            {
                "a": lo,
                "b": hi_acct,
                "a_sold_to_b": a_to_b.astype(np.int64),
                "b_sold_to_a": b_to_a.astype(np.int64),
                "same_side": (~opposite).astype(np.int64),
                "first_seen": ts[i_idx],
                "last_seen": ts[j_idx],
            }
        )
        agg = pairs.groupby(["a", "b"], sort=False).agg(
            a_sold_to_b=("a_sold_to_b", "sum"),
            b_sold_to_a=("b_sold_to_a", "sum"),
            same_side=("same_side", "sum"),
            first_seen=("first_seen", "min"),
            last_seen=("last_seen", "max"),
        ).reset_index()
        agg["security_id"] = int(security_id)

        counts = pd.Series(acct).value_counts()
        span = float(ts[-1] - ts[0]) or 1.0
        expected = _session_aware_expectation(
            agg["a"].map(counts).to_numpy(),
            agg["b"].map(counts).to_numpy(),
            w,
            span,
        )
        buy_share = float(buy.mean())
        p_same = buy_share**2 + (1.0 - buy_share) ** 2
        agg["expected_same"] = expected * p_same
        agg["expected_opposite"] = expected * (1.0 - p_same)
        frames.append(agg)

    if not frames:
        return pd.DataFrame(columns=list(PAIR_COLUMNS))

    out = pd.concat(frames, ignore_index=True)
    out["first_seen"] = pd.to_datetime(out["first_seen"], unit="s", utc=True)
    out["last_seen"] = pd.to_datetime(out["last_seen"], unit="s", utc=True)
    return add_derived(out)


def add_derived(pairs: pd.DataFrame) -> pd.DataFrame:
    """Attach the derived measures every downstream rule uses."""
    out = pairs.copy()
    opposite = out["a_sold_to_b"] + out["b_sold_to_a"]
    out["opposite_total"] = opposite
    hi = np.maximum(out["a_sold_to_b"], out["b_sold_to_a"])
    lo = np.minimum(out["a_sold_to_b"], out["b_sold_to_a"])
    out["reciprocity"] = np.where(hi > 0, lo / np.maximum(hi, 1), 0.0)
    out["opposite_lift"] = opposite / out["expected_opposite"].replace(0, np.nan)
    out["same_lift"] = out["same_side"] / out["expected_same"].replace(0, np.nan)
    return out
