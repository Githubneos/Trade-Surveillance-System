from __future__ import annotations

from datetime import UTC, datetime

from surveillance.generator.ground_truth import ScenarioLabel, read_labels, write_labels
from surveillance.generator.rng import rng_for


def test_named_streams_are_reproducible_and_independent():
    assert rng_for(5, "a").random(4).tolist() == rng_for(5, "a").random(4).tolist()
    assert rng_for(5, "a").random(4).tolist() != rng_for(5, "b").random(4).tolist()
    assert rng_for(5, "a").random(4).tolist() != rng_for(6, "a").random(4).tolist()


def test_stream_naming_is_order_independent():
    """Adding a new scenario must not perturb existing streams. With positional spawning
    it would, and every previously measured metric would silently move."""
    before = rng_for(99, "scenario.wash_ring").random(5).tolist()
    _ = rng_for(99, "scenario.brand_new_thing").random(5)
    after = rng_for(99, "scenario.wash_ring").random(5).tolist()
    assert before == after


def test_label_roundtrip(tmp_path):
    labels = [
        ScenarioLabel(
            scenario_id="wash_ring_01",
            title="Circular trading between 3 accounts in LARK",
            case_ref="SR-2025-0001",
            scenario_type="wash_ring",
            subtype="wash_ring",
            label="positive",
            expected_layer="graph",
            account_ids=[1, 2, 3],
            security_ids=[7],
            window_start=datetime(2025, 3, 4, 15, 0, tzinfo=UTC),
            window_end=datetime(2025, 3, 4, 16, 0, tzinfo=UTC),
            trade_external_ids=["TRD-000000001"],
            difficulty="hard",
            notes="test",
        )
    ]
    path = tmp_path / "gt.jsonl"
    write_labels(labels, path)
    back = read_labels(path)
    assert back == labels
