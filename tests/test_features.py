"""Phase 3: the feature layer must not see the future and must not be absolute.

Both properties are easy to break by accident and impossible to notice from the metrics --
a leaky feature makes every number better, which is exactly why it needs a test.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from surveillance.detect.features import (
    CONTEXT_COLUMNS,
    FEATURE_COLUMNS,
    MIN_HISTORY,
    build_features,
    impute,
)


@pytest.fixture(scope="module")
def features(dataset):
    return build_features(dataset.trades)


def test_features_are_causal(dataset):
    """A trade's features must not change when later trades are removed.

    This is the test that catches look-ahead. A baseline computed over the whole window
    -- the natural way to write it -- silently uses the future, and every metric improves.
    """
    df = dataset.trades.sort_values("executed_at").reset_index(drop=True)
    cutoff = len(df) // 2
    full = build_features(df).iloc[:cutoff]
    truncated = build_features(df.iloc[:cutoff].copy())

    for col in FEATURE_COLUMNS:
        a = full[col].to_numpy(dtype=float)
        b = truncated[col].to_numpy(dtype=float)
        both = ~(np.isnan(a) | np.isnan(b))
        mismatch = np.mean(~np.isclose(a[both], b[both], rtol=1e-6, atol=1e-6))
        assert mismatch == 0.0, f"{col} depends on future trades ({mismatch:.1%})"


def test_a_trade_does_not_contribute_to_its_own_baseline(dataset):
    """z must be computed against strictly prior trades. Including the trade itself drags
    the mean toward it, shrinking the anomaly it is supposed to expose."""
    df = dataset.trades.copy()
    f = build_features(df)
    merged = df.merge(f[["external_id", "z_log_notional"]], on="external_id")
    merged["notional"] = merged["quantity"] * merged["price"]

    # Take the largest trade of a busy account and check z is large, not shrunk.
    counts = merged.groupby("account_id").size()
    busy = counts[counts > 100].index
    sub = merged[merged["account_id"].isin(busy)]
    top = sub.loc[sub["notional"].idxmax()]
    assert abs(top["z_log_notional"]) > 1.5


def test_burst_and_gap_are_account_relative(dataset):
    """The headline rule of the module: nothing absolute.

    A market maker trades two orders of magnitude more than a retail account. If tempo
    features were absolute, every market maker would outrank every retail account forever
    and the feature would encode account type rather than anomaly.
    """
    from surveillance.db.enums import AccountType

    f = impute(build_features(dataset.trades))
    types = {a.id: a.account_type for a in dataset.accounts}
    f = f.copy()
    f["atype"] = f["account_id"].map(types)

    mm = f[f["atype"] == AccountType.MARKET_MAKER]
    retail = f[f["atype"] == AccountType.RETAIL]
    assert len(mm) > 100 and len(retail) > 100

    # Medians should be comparable across account types -- within a factor of three --
    # because each is measured against its own account's norm.
    ratio = mm["burst_ratio"].median() / retail["burst_ratio"].median()
    assert 0.33 < ratio < 3.0, f"burst_ratio encodes account type (ratio {ratio:.2f})"


def test_accounts_without_history_are_neutral_not_anomalous(dataset):
    """A brand-new account must not look suspicious merely for being new."""
    f = build_features(dataset.trades)
    assert (~f["has_history"]).sum() > 0
    filled = impute(f)
    fresh = filled[~f["has_history"]]
    assert np.allclose(fresh["z_log_notional"], 0.0)
    assert np.allclose(fresh["time_of_day_surprise"], 0.0)


def test_min_history_is_respected(dataset):
    f = build_features(dataset.trades)
    counts = f.groupby("account_id").cumcount()
    early = f[counts < MIN_HISTORY]
    assert early["z_log_notional"].isna().all()


def test_context_columns_are_carried_but_not_scored():
    """security_novelty is informative for the graph layer and ruinous as a per-trade
    score -- it flags every account's first trade in any name."""
    assert "security_novelty" in CONTEXT_COLUMNS
    assert "security_novelty" not in FEATURE_COLUMNS


def test_impute_leaves_no_nan_or_inf(dataset):
    filled = impute(build_features(dataset.trades))
    arr = filled[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    assert np.isfinite(arr).all()


def test_features_are_order_independent(dataset):
    """Shuffling input rows must not change any trade's features -- the module sorts
    internally, and a silent dependence on input order would make results irreproducible."""
    df = dataset.trades
    a = build_features(df).set_index("external_id")[list(FEATURE_COLUMNS)]
    shuffled = df.sample(frac=1.0, random_state=1)
    b = build_features(shuffled).set_index("external_id")[list(FEATURE_COLUMNS)]
    pd.testing.assert_frame_equal(a.sort_index(), b.sort_index(), rtol=1e-9, check_like=True)
