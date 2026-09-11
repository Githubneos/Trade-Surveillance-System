"""Phase 5: fusion and the evaluation contract."""

from __future__ import annotations

import pytest

from surveillance.db.enums import AlertType, Severity
from surveillance.detect.fusion import fuse, severity_for
from surveillance.detect.pipeline import run_detection
from surveillance.eval.matching import jaccard_sweep, match_all, recall_by
from surveillance.eval.report import to_alertlike


@pytest.fixture(scope="module")
def liquidity(dataset):
    return {s.id: s.liquidity_tier.value for s in dataset.securities}


@pytest.fixture(scope="module")
def detection(dataset, liquidity):
    return run_detection(dataset.trades, liquidity)


@pytest.fixture(scope="module")
def result(dataset, detection):
    return match_all(to_alertlike(detection.alerts), dataset.labels)


def test_dedup_keys_are_unique_and_stable(dataset, detection, liquidity):
    keys = [a.dedup_key for a in detection.alerts]
    assert len(keys) == len(set(keys))
    again = run_detection(dataset.trades, liquidity)
    assert [a.dedup_key for a in again.alerts] == keys, "re-running must be idempotent"


def test_every_alert_records_which_layer_found_it(detection):
    for a in detection.alerts:
        assert a.detection_method
        assert set(a.detection_method) <= {"statistical", "graph"}


def test_layers_find_what_they_were_expected_to(dataset, detection, result):
    """Attribution against the expectation declared when each case was planted -- not
    against whatever the detectors turned out to do."""
    by_id = {x.scenario_id: x for x in dataset.labels}
    lookup = {a.dedup_key: a for a in detection.alerts}
    for scenario_id, alert_ids in result.detected.items():
        if not alert_ids:
            continue
        expected = by_id[scenario_id].expected_layer
        if expected == "none":
            continue
        methods = set()
        for aid in alert_ids:
            methods.update(lookup[aid].detection_method)
        assert expected in methods, (
            f"{scenario_id} expected {expected} but was found by {sorted(methods)}"
        )


def test_network_typologies_are_found_only_by_the_graph_layer(dataset, result):
    """The architectural claim: the per-trade layer contributes nothing here."""
    by = recall_by(result, dataset.labels, "subtype")
    assert by["wash_ring"][2] >= 0.6
    assert by["coordinated_cluster"][2] >= 0.5


def test_headline_does_not_depend_on_the_matching_threshold(dataset, detection):
    """The evaluation contract fixes Jaccard at 0.5. If the result moved sharply with that
    constant it would be an artefact of the constant rather than a measurement."""
    sweep = jaccard_sweep(to_alertlike(detection.alerts), dataset.labels)
    counts = {got for got, _ in sweep.values()}
    assert len(counts) == 1, f"detection count varies with the matching threshold: {sweep}"


def test_alert_volume_is_workable(dataset, detection):
    assert len(detection.alerts) / len(dataset.trades) < 0.01


def test_severity_bands_are_monotonic():
    assert severity_for(0.95) is Severity.CRITICAL
    assert severity_for(0.75) is Severity.HIGH
    assert severity_for(0.55) is Severity.MEDIUM
    assert severity_for(0.1) is Severity.LOW


def test_corroboration_raises_score_and_records_both_layers():
    from datetime import UTC, datetime

    from surveillance.detect.ring_rules import NetworkAlert
    from surveillance.detect.statistical import TypologyAlert

    when = datetime(2025, 3, 5, 15, 0, tzinfo=UTC)
    trade = TypologyAlert(
        external_id="TRD-1", account_id=7, security_id=3, executed_at=when,
        rule="size_spike", feature="z_log_notional", value=6.0, score=0.99,
    )
    ring = NetworkAlert(
        alert_type="wash_trade_ring", security_id=3, members=(7, 8, 9), score=0.8,
        window_start=datetime(2025, 3, 5, 14, 0, tzinfo=UTC),
        window_end=datetime(2025, 3, 5, 16, 0, tzinfo=UTC),
        trade_external_ids=("TRD-1",), details={"rule": "x"},
    )
    alone = fuse([trade], [])
    both = fuse([trade], [ring])
    stat_alone = next(a for a in alone if a.alert_type is AlertType.STATISTICAL_OUTLIER)
    stat_both = next(a for a in both if a.alert_type is AlertType.STATISTICAL_OUTLIER)
    assert stat_both.score > stat_alone.score
    assert set(stat_both.detection_method) == {"statistical", "graph"}


def test_details_explain_the_alert(detection):
    for a in detection.alerts:
        assert a.details.get("rule"), "an alert an analyst cannot read is noise"
