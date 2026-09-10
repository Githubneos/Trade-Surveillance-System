"""Integration: the generated dataset survives a round trip through Postgres intact,
and no ground truth follows it in.

Marked `integration` because it needs a live database. Run with `pytest -m integration`.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from surveillance.config import Settings
from surveillance.db.loader import load_dataset
from surveillance.db.models import Base
from surveillance.db.session import get_engine
from surveillance.generator.pipeline import GeneratorParams, generate

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get(
    "SURV_TEST_DATABASE_URL", "postgresql+psycopg://localhost:5432/surveillance_test"
)


@pytest.fixture(scope="module")
def session():
    from sqlalchemy.orm import sessionmaker

    engine = get_engine(TEST_DB_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with maker() as s:
        yield s
        s.rollback()


@pytest.fixture(scope="module")
def loaded(session):
    ds = generate(Settings(seed=777), GeneratorParams(n_accounts=120, n_securities=30, n_days=6))
    counts = load_dataset(session, ds)
    session.commit()
    return ds, counts


def test_row_counts_match(session, loaded):
    ds, counts = loaded
    assert counts["trades"] == len(ds.trades)
    assert session.execute(text("select count(*) from trades")).scalar() == len(ds.trades)
    assert session.execute(text("select count(*) from accounts")).scalar() == len(ds.accounts)


def test_generated_notional_column_is_correct(session, loaded):
    """notional is a stored generated column; if the expression were wrong every
    downstream size feature would be silently wrong too."""
    bad = session.execute(
        text("select count(*) from trades where abs(notional - quantity * price) > 1e-6")
    ).scalar()
    assert bad == 0


def test_external_ids_are_unique_in_the_database(session, loaded):
    total, distinct = session.execute(
        text("select count(*), count(distinct external_id) from trades")
    ).one()
    assert total == distinct


def test_no_ground_truth_column_reaches_the_database(session, loaded):
    cols = session.execute(
        text(
            "select column_name from information_schema.columns "
            "where table_schema='public' and table_name='trades'"
        )
    ).scalars().all()
    assert "scenario_id" not in cols
    assert not any("scenario" in c or "label" in c for c in cols)


def test_reload_is_idempotent(session, loaded):
    """Re-loading the same dataset must not duplicate rows -- the loader truncates first,
    and external_id is UNIQUE regardless."""
    ds, _ = loaded
    before = session.execute(text("select count(*) from trades")).scalar()
    load_dataset(session, ds)
    session.commit()
    assert session.execute(text("select count(*) from trades")).scalar() == before
