"""Replays the generated dataset onto a Redis Stream as though it were live order flow.

Replay runs in simulated time: `speed` compresses the original inter-trade gaps, so a
20-day dataset can be pushed through in seconds for tests or paced realistically for a
demo. `speed=0` means "as fast as possible", which is what the ingestion tests use.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

import pandas as pd
import redis

from surveillance.stream.config import STREAM_KEY

#: Fields written to the stream. Deliberately flat strings: Redis Streams values are
#: bytes, and keeping the wire format explicit stops accidental pickling of DataFrame
#: dtypes that would then differ between producer and consumer.
WIRE_FIELDS = (
    "external_id",
    "account_id",
    "security_id",
    "side",
    "quantity",
    "price",
    "executed_at",
    "venue",
    "source",
)


@dataclass(slots=True)
class ProducerStats:
    published: int = 0
    elapsed_s: float = 0.0


def _rows(trades: pd.DataFrame) -> Iterator[dict[str, str]]:
    for r in trades.itertuples(index=False):
        yield {
            "external_id": str(r.external_id),
            "account_id": str(int(r.account_id)),
            "security_id": str(int(r.security_id)),
            "side": str(r.side),
            "quantity": f"{float(r.quantity):.4f}",
            "price": f"{float(r.price):.6f}",
            "executed_at": pd.Timestamp(r.executed_at).isoformat(),
            "venue": str(r.venue),
            "source": str(r.source),
        }


def publish(
    client: redis.Redis,
    trades: pd.DataFrame,
    *,
    speed: float = 0.0,
    batch: int = 1000,
    limit: int | None = None,
    stream_key: str = STREAM_KEY,
) -> ProducerStats:
    """Publish trades in execution order.

    `speed` is a wall-clock compression factor: 60 means one simulated minute per real
    second. 0 disables pacing entirely.
    """
    df = trades.sort_values("executed_at")
    if limit is not None:
        df = df.head(limit)

    stats = ProducerStats()
    started = time.monotonic()
    sim_origin = pd.Timestamp(df["executed_at"].iloc[0]) if len(df) else None

    pipe = client.pipeline(transaction=False)
    pending = 0
    for row in _rows(df):
        if speed > 0 and sim_origin is not None:
            target = (pd.Timestamp(row["executed_at"]) - sim_origin).total_seconds() / speed
            drift = target - (time.monotonic() - started)
            if drift > 0:
                # Flush before sleeping, or paced replay batches up and arrives in bursts.
                if pending:
                    pipe.execute()
                    pending = 0
                time.sleep(min(drift, 1.0))

        pipe.xadd(stream_key, row)
        pending += 1
        stats.published += 1
        if pending >= batch:
            pipe.execute()
            pending = 0

    if pending:
        pipe.execute()
    stats.elapsed_s = time.monotonic() - started
    return stats
