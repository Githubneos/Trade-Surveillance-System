"""End-to-end detection: features -> both layers -> fusion -> alerts."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd

from surveillance.config import Settings, get_settings
from surveillance.detect.fusion import FusedAlert, fuse
from surveillance.detect.graph_builder import build_graphs
from surveillance.detect.ring_rules import NetworkAlert, run_network_detection
from surveillance.detect.statistical import TypologyAlert, per_typology_alerts


@dataclass(slots=True)
class DetectionOutput:
    alerts: list[FusedAlert]
    typology_alerts: list[TypologyAlert]
    network_alerts: list[NetworkAlert]
    ring_pairs: pd.DataFrame
    cluster_pairs: pd.DataFrame


def load_liquidity(settings: Settings | None = None) -> dict[int, str]:
    settings = settings or get_settings()
    data = json.loads(settings.reference_path.read_text())
    return {s["id"]: s["liquidity_tier"] for s in data["securities"]}


def run_detection(
    trades: pd.DataFrame, liquidity: dict[int, str] | None = None
) -> DetectionOutput:
    liquidity = liquidity if liquidity is not None else load_liquidity()
    typology = per_typology_alerts(trades)
    ring_pairs, cluster_pairs = build_graphs(trades, liquidity)
    network = run_network_detection(trades, ring_pairs, cluster_pairs, liquidity)
    return DetectionOutput(
        alerts=fuse(typology, network),
        typology_alerts=typology,
        network_alerts=network,
        ring_pairs=ring_pairs,
        cluster_pairs=cluster_pairs,
    )
