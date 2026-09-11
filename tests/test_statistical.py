"""Phase 3: the per-trade scorer.

The important assertion here is the negative one: this layer must MISS wash rings. If it
starts catching them the planted scenarios have drifted into being individually detectable,
and the graph layer -- the centrepiece of the project -- would be measuring nothing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from surveillance.detect.statistical import (
    TYPOLOGY_RULES,
    per_typology_alerts,
    score_trades,
)
from surveillance.eval.matching import AlertLike, match_all, recall_by


def _alerts(typology_alerts):
    return [
        AlertLike(
            alert_id=f"{a.rule}:{a.external_id}",
            trade_external_ids={a.external_id},
            account_ids={a.account_id},
            window_start=pd.Timestamp(a.executed_at).to_pydatetime(),
            window_end=pd.Timestamp(a.executed_at).to_pydatetime(),
            detection_method=("statistical",),
            alert_type="statistical_outlier",
        )
        for a in typology_alerts
    ]


@pytest.fixture(scope="module")
def outcome(dataset):
    alerts = _alerts(per_typology_alerts(dataset.trades))
    result = match_all(alerts, dataset.labels)
    return alerts, result


def test_rules_catch_the_typologies_they_are_for(dataset, outcome):
    _, result = outcome
    by_subtype = recall_by(result, dataset.labels, "subtype")
    # Thresholds are floors, set below measured performance so ordinary variation does not
    # fail the build while a genuine regression still does.
    assert by_subtype["size_spike"][2] >= 0.6
    assert by_subtype["price_outlier"][2] >= 0.5
    assert by_subtype["frequency_burst"][2] >= 0.75


def test_the_per_trade_layer_misses_wash_rings(dataset, outcome):
    """The load-bearing negative result.

    Wash-ring trades are planted inside each member's own normal size distribution, so no
    per-trade feature separates them. This test failing would mean the rings became
    individually detectable and the graph layer is no longer motivated.
    """
    _, result = outcome
    by_subtype = recall_by(result, dataset.labels, "subtype")
    assert by_subtype["wash_ring"][2] <= 0.2, (
        "the per-trade scorer is catching wash rings; the planted rings have drifted into "
        "being individually anomalous and the graph layer now proves nothing"
    )


def test_alert_volume_stays_workable(dataset, outcome):
    """Precision matters less than volume in compliance: an analyst team has a fixed
    capacity, and a detector that flags 5% of trades is not deployable at any precision."""
    alerts, _ = outcome
    assert len(alerts) / len(dataset.trades) < 0.01


def test_every_alert_explains_itself(dataset):
    """An alert an analyst cannot act on is noise. Each carries the rule that fired, the
    feature it fired on, and the value that breached."""
    for a in per_typology_alerts(dataset.trades)[:200]:
        assert a.rule in TYPOLOGY_RULES
        assert a.feature == TYPOLOGY_RULES[a.rule]["feature"]
        assert np.isfinite(a.value)
        assert 0.0 <= a.score <= 1.0


def test_scorers_are_deterministic(dataset):
    a = score_trades(dataset.trades, scorer="tail_surprisal")
    b = score_trades(dataset.trades, scorer="tail_surprisal")
    assert np.allclose(a.scores.to_numpy(), b.scores.to_numpy())


def test_scores_are_bounded(dataset):
    for name in ("tail_surprisal", "isolation_forest", "robust_z"):
        s = score_trades(dataset.trades, scorer=name).scores.to_numpy()
        assert np.isfinite(s).all()
        assert s.min() >= 0.0 and s.max() <= 1.0


def test_unknown_scorer_is_rejected(dataset):
    with pytest.raises(ValueError, match="unknown scorer"):
        score_trades(dataset.trades.head(100), scorer="magic")
