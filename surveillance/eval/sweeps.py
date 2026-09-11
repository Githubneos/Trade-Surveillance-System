"""Parameter sensitivity sweeps.

A threshold nobody has varied is a threshold nobody has justified. These sweeps exist so
"why this window?" and "what happens if you halve it?" are answerable from measurements
rather than from the confidence of whoever picked the number.

They also reveal the shape of the trade-off, which matters more than the optimum: a metric
that moves sharply with an arbitrary constant is not a measurement, and the only way to
know which kind you have is to vary it and look.
"""

from __future__ import annotations

import pandas as pd

from surveillance.detect.graph_builder import build_pair_graph
from surveillance.detect.ring_rules import run_network_detection
from surveillance.eval.matching import AlertLike, match_all, recall_by


def network_alerts_to_alertlike(alerts) -> list[AlertLike]:
    return [
        AlertLike(
            alert_id=f"net-{i}",
            trade_external_ids=set(a.trade_external_ids),
            account_ids=set(a.members),
            window_start=pd.Timestamp(a.window_start).to_pydatetime(),
            window_end=pd.Timestamp(a.window_end).to_pydatetime(),
            detection_method=("graph",),
            alert_type=a.alert_type,
        )
        for i, a in enumerate(alerts)
    ]


def _score(trades, ring_pairs, cluster_pairs, liquidity, labels) -> dict:
    alerts = run_network_detection(trades, ring_pairs, cluster_pairs, liquidity)
    result = match_all(network_alerts_to_alertlike(alerts), labels)
    by = recall_by(result, labels, "subtype")
    n = len(alerts)
    return {
        "alerts": n,
        "wash_ring": by.get("wash_ring", (0, 0, 0.0))[2],
        "coordinated": by.get("coordinated_cluster", (0, 0, 0.0))[2],
        "precision": len(result.alert_to_scenario) / n if n else 0.0,
        "hard_negatives": len(result.hard_negative_hits),
    }


def sweep_windows(
    trades: pd.DataFrame,
    liquidity: dict[int, str],
    labels,
    *,
    ring_scales=(0.25, 0.5, 1.0, 2.0, 4.0),
    cluster_scales=(0.25, 0.5, 1.0, 2.0, 4.0),
) -> pd.DataFrame:
    """Scale both graphs' windows around their defaults and measure the effect."""
    from surveillance.detect.graph_builder import CLUSTER_WINDOW_BY_TIER, WINDOW_BY_TIER

    rows = []
    base_cluster = build_pair_graph(trades, liquidity, window_by_tier=CLUSTER_WINDOW_BY_TIER)
    for scale in ring_scales:
        tiers = {k: max(int(v * scale), 1) for k, v in WINDOW_BY_TIER.items()}
        pairs = build_pair_graph(trades, liquidity, window_by_tier=tiers)
        rows.append(
            {"graph": "reciprocal", "scale": scale, "window_mid_s": tiers["mid"],
             "pairs": len(pairs), **_score(trades, pairs, base_cluster, liquidity, labels)}
        )

    base_ring = build_pair_graph(trades, liquidity)
    for scale in cluster_scales:
        tiers = {k: max(int(v * scale), 1) for k, v in CLUSTER_WINDOW_BY_TIER.items()}
        pairs = build_pair_graph(trades, liquidity, window_by_tier=tiers)
        rows.append(
            {"graph": "same_side", "scale": scale, "window_mid_s": tiers["mid"],
             "pairs": len(pairs), **_score(trades, base_ring, pairs, liquidity, labels)}
        )
    return pd.DataFrame(rows)


def sweep_resolution(
    trades: pd.DataFrame,
    ring_pairs: pd.DataFrame,
    cluster_pairs: pd.DataFrame,
    liquidity: dict[int, str],
    labels,
    values=(0.6, 0.8, 1.0, 1.2, 1.4, 1.8, 2.4),
) -> pd.DataFrame:
    """Louvain resolution, which controls how small a community may be.

    Modularity has a documented resolution limit: communities below a certain size relative
    to the graph get absorbed into larger ones. A three-account ring in a graph of hundreds
    is squarely in that regime, so this parameter is load-bearing, not cosmetic.
    """
    rows = []
    for r in values:
        alerts = run_network_detection(
            trades, ring_pairs, cluster_pairs, liquidity, ring={"resolution": r}
        )
        result = match_all(network_alerts_to_alertlike(alerts), labels)
        by = recall_by(result, labels, "subtype")
        rows.append(
            {
                "resolution": r,
                "alerts": len(alerts),
                "wash_ring": by.get("wash_ring", (0, 0, 0.0))[2],
                "precision": (
                    len(result.alert_to_scenario) / len(alerts) if alerts else 0.0
                ),
                "hard_negatives": len(result.hard_negative_hits),
            }
        )
    return pd.DataFrame(rows)
