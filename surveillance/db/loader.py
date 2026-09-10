"""Bulk-load a generated dataset into Postgres.

Note what is NOT loaded: ``scenario_id``. Ground-truth labels live only in
``data/ground_truth.jsonl`` and never enter the database, so nothing in the serving path
-- API, detectors, dashboard -- can read them, deliberately or by accident.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from surveillance.generator.writer import Dataset, db_frame

TRADE_COPY_COLUMNS = (
    "external_id",
    "account_id",
    "security_id",
    "side",
    "quantity",
    "price",
    "executed_at",
    "venue",
    "source",
    "counterparty_account_id",
)


def truncate_all(session: Session) -> None:
    session.execute(
        text(
            "TRUNCATE alert_trades, alert_accounts, alerts, graph_community_events, "
            "graph_snapshots, trades, securities, accounts RESTART IDENTITY CASCADE"
        )
    )


def _copy(session: Session, table: str, df: pd.DataFrame, columns: tuple[str, ...]) -> int:
    """COPY via psycopg's binary-free text path. Roughly 30x faster than ORM inserts for
    this volume, and the loader is run often enough during development to care."""
    raw = session.connection().connection
    buf = df[list(columns)].replace({np.nan: None})
    with raw.cursor() as cur, cur.copy(
        f"COPY {table} ({', '.join(columns)}) FROM STDIN"
    ) as copy:
        for row in buf.itertuples(index=False, name=None):
            copy.write_row(row)
    return len(df)


def load_dataset(session: Session, ds: Dataset, *, truncate: bool = True) -> dict[str, int]:
    if truncate:
        truncate_all(session)

    accounts = pd.DataFrame([a.to_row() for a in ds.accounts])
    securities = pd.DataFrame([s.to_row() for s in ds.securities])[
        ["id", "ticker", "name", "sector", "liquidity_tier", "adv", "reference_price"]
    ]

    counts = {
        "accounts": _copy(
            session, "accounts", accounts,
            ("id", "external_ref", "name", "account_type", "risk_tier"),
        ),
        "securities": _copy(
            session, "securities", securities,
            ("id", "ticker", "name", "sector", "liquidity_tier", "adv", "reference_price"),
        ),
        "trades": _copy(session, "trades", db_frame(ds.trades), TRADE_COPY_COLUMNS),
    }

    # COPY bypasses the sequences, so bump them past the ids we inserted.
    for table in ("accounts", "securities", "trades"):
        session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 1))"
            )
        )
    return counts
