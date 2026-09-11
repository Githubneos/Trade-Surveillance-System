"""Phase 4: the graph layer.

The central claim of the project is verified here: wash rings that no per-trade feature can
separate ARE separable as network structure, using only inferred relationships.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from surveillance.detect.graph_builder import build_graphs, build_pair_graph
from surveillance.detect.ring_rules import run_network_detection
from surveillance.eval.matching import match_all, recall_by
from surveillance.eval.sweeps import network_alerts_to_alertlike


@pytest.fixture(scope="module")
def liquidity(dataset):
    return {s.id: s.liquidity_tier.value for s in dataset.securities}


@pytest.fixture(scope="module")
def graphs(dataset, liquidity):
    return build_graphs(dataset.trades, liquidity)


@pytest.fixture(scope="module")
def outcome(dataset, graphs, liquidity):
    ring_pairs, cluster_pairs = graphs
    alerts = run_network_detection(dataset.trades, ring_pairs, cluster_pairs, liquidity)
    return alerts, match_all(network_alerts_to_alertlike(alerts), dataset.labels)


def test_graph_finds_what_the_per_trade_layer_cannot(dataset, outcome):
    """The project's central claim, as a test.

    Wash rings are planted so that every trade is ordinary for the account that placed it;
    tests/test_statistical.py asserts the per-trade layer misses them. They must still be
    recoverable from structure alone.
    """
    _, result = outcome
    by = recall_by(result, dataset.labels, "subtype")
    assert by["wash_ring"][2] >= 0.6, (
        "the graph layer cannot find rings the per-trade layer also misses -- the two-layer "
        "architecture is then unjustified"
    )


def test_coordinated_clusters_are_detected(dataset, outcome):
    _, result = outcome
    by = recall_by(result, dataset.labels, "subtype")
    assert by["coordinated_cluster"][2] >= 0.5


def test_timestamps_are_resolution_safe(dataset, liquidity):
    """Guards a bug that produced a healthy-looking but meaningless graph.

    The parquet round-trips as datetime64[ms]; code assuming nanoseconds divided epoch
    seconds by a billion, collapsing every security's span to about two seconds so every
    trade neighboured every other. Nothing looked wrong -- lift came out at a tidy 1.0
    because the null model was correctly calibrated against nonsense.
    """
    df = dataset.trades.copy()
    pairs_ms = build_pair_graph(df, liquidity)
    df_ns = df.copy()
    df_ns["executed_at"] = pd.to_datetime(df_ns["executed_at"], utc=True).astype(
        "datetime64[ns, UTC]"
    )
    pairs_ns = build_pair_graph(df_ns, liquidity)
    assert len(pairs_ms) == len(pairs_ns)
    # And the window must actually be selective: a pair count near n^2 means it is not.
    assert len(pairs_ms) < len(df) ** 0.9


def test_reciprocity_separates_rings_from_market_makers(dataset, graphs):
    """The discriminator the whole ring rule rests on.

    A market maker's flow is bidirectional too. What differs is balance per counterparty:
    a ring recycles a position and nets flat, an MM absorbs client demand and ends
    directional.
    """
    ring_pairs, _ = graphs
    labels = {x.scenario_id: x for x in dataset.labels}
    ring_scores, mm_scores = [], []
    for label in labels.values():
        if label.scenario_type not in ("wash_ring", "mm_two_sided"):
            continue
        sec = label.security_ids[0]
        members = set(label.account_ids)
        sub = ring_pairs[ring_pairs["security_id"] == sec]
        inside = sub[sub["a"].isin(members) & sub["b"].isin(members)]
        if inside.empty:
            continue
        target = ring_scores if label.scenario_type == "wash_ring" else mm_scores
        target.append(float(inside["reciprocity"].median()))

    assert ring_scores and mm_scores
    assert np.median(ring_scores) > np.median(mm_scores) + 0.2


def test_no_alert_is_raised_without_evidence(dataset, outcome):
    alerts, _ = outcome
    for a in alerts:
        assert a.members and len(a.members) >= 3
        assert a.trade_external_ids
        assert 0.0 <= a.score <= 1.0
        assert a.details.get("rule")
        assert a.window_start <= a.window_end


def test_alert_volume_is_investigable(dataset, outcome):
    """Network alerts name whole groups, so each is expensive to investigate. A layer that
    emits hundreds of them is not usable regardless of its recall."""
    alerts, _ = outcome
    assert len(alerts) < 60


def test_detection_is_deterministic(dataset, graphs, liquidity):
    ring_pairs, cluster_pairs = graphs
    a = run_network_detection(dataset.trades, ring_pairs, cluster_pairs, liquidity)
    b = run_network_detection(dataset.trades, ring_pairs, cluster_pairs, liquidity)
    assert [(x.alert_type, x.members) for x in a] == [(x.alert_type, x.members) for x in b]


def test_graph_layer_never_reads_counterparty(dataset, liquidity):
    """Belt and braces alongside the AST guard: strip the column entirely and confirm the
    results are identical. If anything reached for it, this would change."""
    stripped = dataset.trades.drop(columns=["counterparty_account_id"], errors="ignore")
    ring_a, cluster_a = build_graphs(dataset.trades, liquidity)
    ring_b, cluster_b = build_graphs(stripped, liquidity)
    assert len(ring_a) == len(ring_b)
    alerts_a = run_network_detection(dataset.trades, ring_a, cluster_a, liquidity)
    alerts_b = run_network_detection(stripped, ring_b, cluster_b, liquidity)
    assert [x.members for x in alerts_a] == [x.members for x in alerts_b]


def test_json_details_are_serialisable(dataset, outcome):
    """Details land in a JSONB column and in the API response."""
    alerts, _ = outcome
    for a in alerts:
        json.dumps(a.details)
