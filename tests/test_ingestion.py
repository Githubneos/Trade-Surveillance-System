"""Phase 2: the ingestion layer must not lose or duplicate a trade, even mid-crash.

The headline test kills a consumer with SIGKILL -- uncatchable, no cleanup, no flush --
while it is actively writing, then restarts it and checks the books balance. That is the
only honest way to test a durability claim: a consumer that is asked politely to stop
proves nothing about what happens when a pod is evicted.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pandas as pd
import pytest
import redis
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from surveillance.config import Settings
from surveillance.db.loader import load_dataset
from surveillance.db.models import Base
from surveillance.db.session import get_engine
from surveillance.generator.pipeline import GeneratorParams, generate
from surveillance.stream.consumer import consume, ensure_group, write_batch
from surveillance.stream.producer import publish

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get(
    "SURV_TEST_DATABASE_URL", "postgresql+psycopg://localhost:5432/surveillance_test"
)
REDIS_URL = os.environ.get("SURV_REDIS_URL", "redis://localhost:6379/0")
STREAM = "test_trades"
GROUP = "test_ingest"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def client():
    c = redis.from_url(REDIS_URL)
    try:
        c.ping()
    except redis.ConnectionError:  # pragma: no cover
        pytest.skip("redis not available")
    yield c
    c.delete(STREAM)


@pytest.fixture(scope="module")
def engine():
    eng = get_engine(TEST_DB_URL)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture(scope="module")
def maker(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture(scope="module")
def dataset():
    return generate(Settings(seed=4242), GeneratorParams(n_accounts=60, n_securities=20, n_days=3))


@pytest.fixture
def seeded(engine, maker, dataset, client):
    """Reference data loaded, trades table empty, stream freshly populated."""
    client.delete(STREAM)
    with maker() as s:
        load_dataset(s, dataset)
        s.execute(text("TRUNCATE trades RESTART IDENTITY CASCADE"))
        s.commit()
    ensure_group(client, STREAM, GROUP)
    return dataset


def _count(maker) -> tuple[int, int]:
    with maker() as s:
        return s.execute(
            text("SELECT count(*), count(DISTINCT external_id) FROM trades")
        ).one()


def test_clean_run_ingests_every_trade(client, maker, seeded):
    trades = seeded.trades.head(2000)
    publish(client, trades, stream_key=STREAM)
    stats = consume(
        client, maker, batch_size=250, idle_exit_s=1.0, block_ms=200,
        stream_key=STREAM, group=GROUP, claim_min_idle_ms=100,
    )
    total, distinct = _count(maker)
    assert stats.inserted == len(trades)
    assert total == distinct == len(trades)


def test_redelivery_is_absorbed_by_the_idempotent_write(client, maker, seeded):
    """Simulates a crash between COMMIT and XACK: the same batch arrives twice.

    This is the exact window the ack-after-commit ordering leaves open, and the UNIQUE
    external_id plus ON CONFLICT DO NOTHING is what makes it harmless.
    """
    trades = seeded.trades.head(300)
    messages = [
        (
            f"0-{i}",
            {
                "external_id": r.external_id,
                "account_id": str(int(r.account_id)),
                "security_id": str(int(r.security_id)),
                "side": str(r.side),
                "quantity": f"{float(r.quantity):.4f}",
                "price": f"{float(r.price):.6f}",
                "executed_at": pd.Timestamp(r.executed_at).isoformat(),
                "venue": str(r.venue),
                "source": "synthetic",
            },
        )
        for i, r in enumerate(trades.itertuples(index=False))
    ]

    with maker() as s:
        inserted, dupes = write_batch(s, messages)
        s.commit()
    assert (inserted, dupes) == (len(messages), 0)

    with maker() as s:
        inserted2, dupes2 = write_batch(s, messages)
        s.commit()
    assert inserted2 == 0, "redelivery must insert nothing"
    assert dupes2 == len(messages)

    total, distinct = _count(maker)
    assert total == distinct == len(messages)


def test_sigkilled_consumer_loses_and_duplicates_nothing(client, maker, seeded):
    """Kill the consumer mid-stream, restart it, and check the books balance."""
    # The victim is throttled so the kill is deterministic. Earlier versions raced: the
    # consumer drained the stream faster than the test could deliver a signal, so under
    # load the kill landed after completion and the test quietly stopped exercising
    # recovery at all -- passing while asserting nothing.
    trades = seeded.trades.head(4000)
    publish(client, trades, stream_key=STREAM)
    assert client.xlen(STREAM) == len(trades)

    env = {
        **os.environ,
        "SURV_DATABASE_URL": TEST_DB_URL,
        "SURV_REDIS_URL": REDIS_URL,
        "SURV_STREAM_KEY": STREAM,
        "SURV_CONSUMER_GROUP": GROUP,
        "SURV_CLAIM_MIN_IDLE_MS": "100",
        "PYTHONPATH": REPO,
    }
    cmd = [
        sys.executable, "-m", "surveillance.cli", "consume",
        "--name", "victim", "--batch", "100", "--idle-exit", "30", "--delay-ms", "40",
    ]
    proc = subprocess.Popen(cmd, env=env, cwd=REPO, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        # Wait until it is demonstrably mid-flight: some rows in, but not all.
        deadline = time.monotonic() + 60
        progressed = 0
        # Kill early in the run. With the throttle the consumer needs seconds to reach
        # this point, so the signal cannot arrive after completion.
        ceiling = int(len(trades) * 0.5)
        while time.monotonic() < deadline:
            progressed, _ = _count(maker)
            if 0 < progressed < ceiling:
                break
            time.sleep(0.01)
        assert 0 < progressed < ceiling, (
            f"consumer never observed mid-stream (rows={progressed}/{len(trades)})"
        )
    finally:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

    killed_at, _ = _count(maker)
    assert killed_at < len(trades), "test is meaningless if the run had already finished"

    # A second consumer takes over. It must reclaim whatever the corpse was holding.
    stats = consume(
        client, maker, consumer_name="survivor", batch_size=250, idle_exit_s=2.0,
        block_ms=200, stream_key=STREAM, group=GROUP, claim_min_idle_ms=100,
    )

    total, distinct = _count(maker)
    assert total == len(trades), f"lost {len(trades) - total} trades across the crash"
    assert total == distinct, "duplicate external_id after recovery"
    assert client.xpending(STREAM, GROUP)["pending"] == 0, "messages stranded in the PEL"
    assert stats.reclaimed > 0, "recovery did not go through XAUTOCLAIM as intended"


def test_failed_batch_leaves_messages_pending_and_trips_the_breaker(client, maker, seeded):
    """A DB error must NOT ack, or the batch is acknowledged and lost forever.

    It must also not retry forever: an unacked batch is reclaimable, so without a circuit
    breaker a poison message pins the consumer in a hot loop making no progress. The
    breaker turns that into a visibly dead worker, which a supervisor can act on.
    """
    trades = seeded.trades.head(50)
    publish(client, trades, stream_key=STREAM)

    class Exploding:
        def __call__(self):
            return self

        def execute(self, *a, **k):
            raise RuntimeError("simulated database failure")

        def commit(self):  # pragma: no cover
            raise AssertionError("must not commit")

        def rollback(self):
            pass

        def close(self):
            pass

    stats = consume(
        client, Exploding(), batch_size=25, idle_exit_s=1.0, block_ms=200,
        stream_key=STREAM, group=GROUP, claim_min_idle_ms=100,
        max_consecutive_failures=3,
    )
    assert stats.inserted == 0
    assert stats.errors
    assert stats.gave_up, "consumer must stop rather than retry a poison batch forever"
    assert stats.failed_batches == 3
    assert client.xpending(STREAM, GROUP)["pending"] > 0, "failed batch was wrongly acked"

    # And a healthy consumer then recovers all of it.
    consume(
        client, maker, consumer_name="healer", batch_size=50, idle_exit_s=2.0,
        block_ms=200, stream_key=STREAM, group=GROUP, claim_min_idle_ms=100,
    )
    total, distinct = _count(maker)
    assert total == distinct == len(trades)
