"""Phase 8: SEC EDGAR news correlation.

Runs entirely offline against cached filings. A test suite that needs the internet is a
test suite that fails in CI for reasons unrelated to the code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from surveillance.news.correlation import (
    REFERENCE_CIKS,
    assign_ciks,
    correlate,
    materiality_scores,
)
from surveillance.news.edgar import EdgarClient, describe_items


def test_item_codes_become_prose():
    """TF-IDF needs words. '2.02' carries no lexical signal; its expansion does."""
    assert "results of operations" in describe_items("2.02")
    assert "financial statements" in describe_items("2.02,9.01")
    assert describe_items("") == "material event disclosure"
    assert "item 99.9" in describe_items("99.9")


def test_materiality_ranks_price_sensitive_events_above_routine_ones():
    """The whole point of the materiality weight: not every 8-K moves a price."""
    scores = materiality_scores(
        [
            describe_items("2.02"),  # earnings
            describe_items("2.01"),  # acquisition completed
            describe_items("5.02"),  # a director resigned
            describe_items("5.03"),  # bylaw amendment
        ]
    )
    earnings, acquisition, director, bylaws = scores
    assert earnings > director
    assert acquisition > bylaws


def test_cik_assignment_is_deterministic_and_stable():
    """Hash-based rather than positional, so adding a security does not reshuffle the rest
    -- and so the mapping is obviously arbitrary, which it is."""
    a = assign_ciks([1, 2, 3, 4, 5])
    b = assign_ciks([1, 2, 3, 4, 5])
    assert a == b
    extended = assign_ciks([1, 2, 3, 4, 5, 6, 7])
    assert all(extended[k] == a[k] for k in a)
    assert set(a.values()) <= set(REFERENCE_CIKS)


def test_offline_client_makes_no_requests(tmp_path):
    client = EdgarClient(cache_dir=tmp_path, offline=True)
    assert client.recent_8k(320193) == []


def test_client_reads_from_cache_when_present():
    cache = Path("data/edgar_cache")
    if not cache.exists() or not any(cache.glob("*.json")):
        pytest.skip("no cached filings available")
    client = EdgarClient(cache_dir=cache, offline=True)
    cik = int(next(cache.glob("*.json")).stem.split("-")[1])
    filings = client.recent_8k(cik, limit=5)
    assert filings
    assert all(f.form == "8-K" for f in filings)
    assert all(f.headline for f in filings)


class _Filing:
    def __init__(self, filed_at, headline):
        self.cik = 1
        self.company = "Test Co"
        self.form = "8-K"
        self.filed_at = filed_at
        self.accession = "x"
        self.headline = headline


def _trades(n: int, start: datetime, security_id: int = 1) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "security_id": [security_id] * n,
            "executed_at": [start + timedelta(minutes=5 * i) for i in range(n)],
            "account_id": list(range(n)),
            "side": ["buy"] * n,
            "quantity": [10.0] * n,
            "price": [100.0] * n,
        }
    )


def test_normal_activity_does_not_breach():
    """The expected result on unrelated data, and the one that must not produce alerts."""
    start = datetime(2025, 3, 1, tzinfo=UTC)
    trades = _trades(600, start)
    filed = start + timedelta(days=1)
    signals = correlate(trades, {1: [_Filing(filed, describe_items("2.02"))]}, {1: "TEST"})
    assert signals, "an in-window filing must be evaluated even when it does not breach"
    assert not any(s.flagged for s in signals)


def test_accumulation_before_a_material_filing_is_flagged():
    """Positive control: the signal must fire when the pattern it looks for is present."""
    start = datetime(2025, 3, 1, tzinfo=UTC)
    quiet = _trades(100, start)
    filed = start + timedelta(days=10)
    burst = _trades(400, filed - timedelta(hours=20))
    trades = pd.concat([quiet, burst], ignore_index=True)
    signals = correlate(trades, {1: [_Filing(filed, describe_items("2.01"))]}, {1: "TEST"})
    assert any(s.flagged for s in signals)
    hit = next(s for s in signals if s.flagged)
    assert hit.activity_lift >= 2.0
    assert hit.materiality >= 0.15


def test_a_routine_filing_is_not_flagged_however_busy_the_window():
    """Materiality is a gate, not decoration. Heavy trading before a bylaw amendment is
    not evidence of anything."""
    start = datetime(2025, 3, 1, tzinfo=UTC)
    quiet = _trades(100, start)
    filed = start + timedelta(days=10)
    burst = _trades(400, filed - timedelta(hours=20))
    trades = pd.concat([quiet, burst], ignore_index=True)
    signals = correlate(trades, {1: [_Filing(filed, describe_items("5.03"))]}, {1: "TEST"})
    assert not any(s.flagged for s in signals)


def test_filings_outside_the_window_are_ignored():
    start = datetime(2025, 3, 1, tzinfo=UTC)
    trades = _trades(200, start)
    far_future = start + timedelta(days=400)
    signals = correlate(trades, {1: [_Filing(far_future, describe_items("2.02"))]}, {1: "TEST"})
    assert signals == []
