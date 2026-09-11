"""Redis Streams consumer that writes trades to Postgres without loss or duplication.

## The exactly-once argument

Redis Streams give **at-least-once** delivery. Combined with an **idempotent write**, that
is enough -- and it is the standard way this is done, because true exactly-once delivery
across two systems is not achievable without a distributed transaction.

The write is idempotent because ``trades.external_id`` is UNIQUE and inserts use
``ON CONFLICT DO NOTHING``. The ordering is what makes it safe:

    1. read a batch    (XREADGROUP)
    2. insert + COMMIT (idempotent)
    3. acknowledge     (XACK)

Consider every place a crash can land:

* **Between 1 and 2** -- nothing was written; the messages stay in the consumer group's
  pending entries list (PEL) and are recovered by ``XAUTOCLAIM``. No loss.
* **Between 2 and 3** -- the rows are committed but unacknowledged. They are redelivered,
  re-inserted, and ``ON CONFLICT`` absorbs every one. No duplicates.

Acknowledging *before* committing would invert this and silently lose a batch on every
crash, which is the bug this ordering exists to prevent.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import time
from dataclasses import dataclass, field

import redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from surveillance.stream.config import (
    CLAIM_MIN_IDLE_MS,
    CONSUMER_GROUP,
    DEFAULT_BATCH,
    STREAM_KEY,
)

log = logging.getLogger(__name__)

INSERT_SQL = text(
    """
    INSERT INTO trades
        (external_id, account_id, security_id, side, quantity, price,
         executed_at, venue, source)
    VALUES
        (:external_id, :account_id, :security_id, CAST(:side AS side), :quantity, :price,
         CAST(:executed_at AS timestamptz), :venue, :source)
    ON CONFLICT (external_id) DO NOTHING
    """
)


@dataclass(slots=True)
class ConsumerStats:
    received: int = 0
    inserted: int = 0
    duplicates: int = 0
    reclaimed: int = 0
    batches: int = 0
    failed_batches: int = 0
    gave_up: bool = False
    errors: list[str] = field(default_factory=list)


def ensure_group(client: redis.Redis, stream_key: str = STREAM_KEY, group: str = CONSUMER_GROUP):
    """Create the consumer group, tolerating the case where it already exists.

    ``mkstream=True`` also creates the stream, so a consumer can be started before the
    producer without racing.
    """
    try:
        client.xgroup_create(stream_key, group, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def _decode(raw: dict) -> dict:
    out = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        out[key] = val
    return out


def _to_params(fields: dict) -> dict:
    return {
        "external_id": fields["external_id"],
        "account_id": int(fields["account_id"]),
        "security_id": int(fields["security_id"]),
        "side": fields["side"],
        "quantity": float(fields["quantity"]),
        "price": float(fields["price"]),
        "executed_at": fields["executed_at"],
        "venue": fields.get("venue", "XNAS"),
        "source": fields.get("source", "synthetic"),
    }


def write_batch(session: Session, messages: list[tuple[str, dict]]) -> tuple[int, int]:
    """Insert a batch idempotently. Returns (inserted, duplicates).

    The count comes from comparing the table's row count either side of the insert rather
    than from ``rowcount``: with ``ON CONFLICT DO NOTHING`` and ``executemany``, drivers
    report rowcount inconsistently, and a duplicate count that is quietly wrong would
    undermine the very property this module exists to demonstrate.
    """
    if not messages:
        return 0, 0
    params = [_to_params(fields) for _, fields in messages]
    before = session.execute(text("SELECT count(*) FROM trades")).scalar_one()
    session.execute(INSERT_SQL, params)
    after = session.execute(text("SELECT count(*) FROM trades")).scalar_one()
    inserted = int(after - before)
    return inserted, len(params) - inserted


class _Stopper:
    """Cooperative shutdown. SIGTERM finishes the current batch, then exits cleanly --
    SIGKILL is what the crash test uses, and is deliberately not catchable."""

    def __init__(self) -> None:
        self.stop = False
        for sig in (signal.SIGINT, signal.SIGTERM):
            # Not on the main thread (e.g. under a test runner) -> no signal handling.
            with contextlib.suppress(ValueError):
                signal.signal(sig, self._handle)

    def _handle(self, *_: object) -> None:
        self.stop = True


def consume(
    client: redis.Redis,
    session_factory,
    *,
    consumer_name: str = "worker-1",
    batch_size: int = DEFAULT_BATCH,
    block_ms: int = 2000,
    max_messages: int | None = None,
    idle_exit_s: float | None = None,
    max_consecutive_failures: int = 5,
    batch_delay_s: float = 0.0,
    stream_key: str = STREAM_KEY,
    group: str = CONSUMER_GROUP,
    claim_min_idle_ms: int = CLAIM_MIN_IDLE_MS,
    on_batch=None,
) -> ConsumerStats:
    """Run the consumer loop until stopped, drained, or ``max_messages`` is reached.

    ``on_batch`` is called after each committed batch with the inserted rows, which is how
    the detection pipeline and the WebSocket fan-out hook in without this module knowing
    anything about them.

    ``max_consecutive_failures`` is a circuit breaker. A batch that fails is deliberately
    not acked so it can be retried -- but that means a permanently failing batch (a poison
    message, or a database that is down) would be reclaimed and retried forever, spinning
    the consumer hot and making no progress. After this many consecutive failures the loop
    exits with ``gave_up`` set, so a supervisor sees a crashed worker rather than a healthy
    one burning CPU in silence.
    """
    ensure_group(client, stream_key, group)
    stats = ConsumerStats()
    stopper = _Stopper()
    last_progress = time.monotonic()
    consecutive_failures = 0

    while not stopper.stop:
        if max_messages is not None and stats.received >= max_messages:
            break

        # Recover anything a previous consumer died holding, before taking new work.
        # Doing this first means a restart drains the backlog rather than racing ahead
        # and leaving orphans stuck in the PEL indefinitely.
        claimed: list[tuple[str, dict]] = []
        try:
            _, pending, _ = client.xautoclaim(
                stream_key,
                group,
                consumer_name,
                claim_min_idle_ms,
                start_id="0-0",
                count=batch_size,
            )
            claimed = [
                (mid.decode() if isinstance(mid, bytes) else mid, _decode(fields))
                for mid, fields in pending
                if fields
            ]
            stats.reclaimed += len(claimed)
        except redis.ResponseError as exc:  # pragma: no cover - old Redis without XAUTOCLAIM
            stats.errors.append(f"xautoclaim: {exc}")

        messages = list(claimed)
        if len(messages) < batch_size:
            resp = client.xreadgroup(
                group,
                consumer_name,
                {stream_key: ">"},
                count=batch_size - len(messages),
                block=block_ms,
            )
            for _stream, entries in resp or []:
                for mid, fields in entries:
                    mid_s = mid.decode() if isinstance(mid, bytes) else mid
                    messages.append((mid_s, _decode(fields)))

        if not messages:
            if idle_exit_s is not None and time.monotonic() - last_progress > idle_exit_s:
                break
            continue

        last_progress = time.monotonic()
        stats.received += len(messages)
        stats.batches += 1

        session = session_factory()
        try:
            inserted, duplicates = write_batch(session, messages)
            session.commit()  # (2) durable BEFORE the ack
        except Exception as exc:
            session.rollback()
            stats.errors.append(str(exc))
            stats.failed_batches += 1
            consecutive_failures += 1
            log.exception("batch failed; leaving messages pending for redelivery")
            # Deliberately no XACK: the messages stay in the PEL and get another chance.
            if consecutive_failures >= max_consecutive_failures:
                stats.gave_up = True
                log.error(
                    "giving up after %d consecutive failed batches", consecutive_failures
                )
                break
            # Back off so a failing dependency is not hammered.
            time.sleep(min(0.1 * 2**consecutive_failures, 2.0))
            continue
        finally:
            session.close()

        consecutive_failures = 0
        if batch_delay_s:
            # Deliberate throttle. Useful for a paced demo, and it makes the crash-recovery
            # test deterministic: without it the consumer drains the stream faster than a
            # test can reliably deliver a signal, so the kill lands after completion and
            # the test silently stops exercising recovery.
            time.sleep(batch_delay_s)
        stats.inserted += inserted
        stats.duplicates += duplicates
        client.xack(stream_key, group, *[mid for mid, _ in messages])  # (3)

        if on_batch is not None:
            on_batch(messages, inserted)

    return stats
