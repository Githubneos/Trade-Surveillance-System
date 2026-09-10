"""Assemble the full dataset and persist it.

One detail here is load-bearing for honest evaluation: ``external_id`` is assigned only
*after* all trades -- background and planted -- have been merged and sorted by execution
time. If ids were handed out per component, background trades would occupy one contiguous
id range and planted anomalies another, and the identifier itself would leak the label.
Sorting first makes ids monotonic in time (as a real venue's would be) and uninformative.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from surveillance.generator import background as bg
from surveillance.generator.ground_truth import ScenarioLabel, write_labels
from surveillance.generator.market import MINUTES_PER_SESSION, MarketData, session_start
from surveillance.generator.reference import AccountRef, SecurityRef
from surveillance.generator.rng import rng_for

VENUES = ("XNAS", "XNYS", "BATS", "IEXG")


@dataclass(slots=True)
class Dataset:
    trades: pd.DataFrame
    labels: list[ScenarioLabel]
    accounts: list[AccountRef]
    securities: list[SecurityRef]


def _to_timestamps(market: MarketData, day_idx: np.ndarray, minute: np.ndarray, second: np.ndarray):
    """Vectorised local-session -> UTC conversion, DST-correct via zoneinfo per day."""
    day_epoch = np.array(
        [session_start(d).astimezone(UTC).timestamp() for d in market.days], dtype=np.int64
    )
    epoch = day_epoch[day_idx] + minute.astype(np.int64) * 60 + second.astype(np.int64)
    return pd.to_datetime(epoch, unit="s", utc=True)


def build_dataset(
    master_seed: int,
    market: MarketData,
    accounts: list[AccountRef],
    securities: list[SecurityRef],
    records: dict[str, list],
    labels: list[ScenarioLabel],
) -> Dataset:
    df = pd.DataFrame(records)
    df["day_idx"] = df["day_idx"].astype(np.int32)
    df["minute"] = df["minute"].astype(np.int32)
    df["second"] = df["second"].astype(np.int32)
    df["executed_at"] = _to_timestamps(
        market, df["day_idx"].to_numpy(), df["minute"].to_numpy(), df["second"].to_numpy()
    )

    # Sort by time, then by a stable tiebreak, THEN assign ids. See module docstring.
    df = df.sort_values(
        ["executed_at", "security_id", "account_id"], kind="stable"
    ).reset_index(drop=True)
    df["external_id"] = [f"TRD-{i:09d}" for i in range(1, len(df) + 1)]

    rng = rng_for(master_seed, "writer.venues")
    df["venue"] = rng.choice(VENUES, size=len(df), p=[0.42, 0.34, 0.16, 0.08])
    df["source"] = "synthetic"
    df["price"] = df["price"].astype(float).round(4)
    df["quantity"] = df["quantity"].astype(float)
    df["counterparty_account_id"] = df["counterparty_account_id"].astype("Int64")

    # Backfill the label -> trade mapping now that ids exist.
    by_scenario: dict[str, list[str]] = {}
    mask = df["scenario_id"].notna()
    for sid, ext in zip(df.loc[mask, "scenario_id"], df.loc[mask, "external_id"], strict=True):
        by_scenario.setdefault(str(sid), []).append(str(ext))
    for label in labels:
        label.trade_external_ids = by_scenario.get(label.scenario_id, [])

    return Dataset(trades=df, labels=labels, accounts=accounts, securities=securities)


def persist(ds: Dataset, settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    # The parquet keeps scenario_id so the dataset is self-describing on disk, but the
    # database loader drops it -- labels never reach the serving path.
    ds.trades.to_parquet(settings.trades_path, index=False)
    write_labels(ds.labels, settings.ground_truth_path)

    reference = {
        "generated_at": datetime.now(UTC).isoformat(),
        "seed": settings.seed,
        "accounts": [a.to_row() for a in ds.accounts],
        "securities": [s.to_row() for s in ds.securities],
    }
    settings.reference_path.write_text(json.dumps(reference, indent=2))


def db_frame(trades: pd.DataFrame) -> pd.DataFrame:
    """The columns that actually go into the trades table -- scenario_id excluded."""
    cols = [
        "external_id", "account_id", "security_id", "side", "quantity", "price",
        "executed_at", "venue", "source", "counterparty_account_id",
    ]
    return trades[cols]


__all__ = ["Dataset", "build_dataset", "persist", "db_frame", "bg", "MINUTES_PER_SESSION"]
