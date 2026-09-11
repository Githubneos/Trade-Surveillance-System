"""Stream topology constants, in one place so the producer, consumer and tests agree."""

from __future__ import annotations

STREAM_KEY = "trades"
CONSUMER_GROUP = "ingest"
#: Messages pending longer than this are assumed to belong to a dead consumer and are
#: reclaimed. Must exceed the worst-case time to write one batch, or a slow-but-alive
#: consumer has its work stolen and does it twice (harmless here thanks to idempotent
#: writes, but it wastes capacity and muddies the pending metrics).
CLAIM_MIN_IDLE_MS = 30_000
DEFAULT_BATCH = 500
