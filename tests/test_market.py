"""Price-path and calendar properties."""

from __future__ import annotations

from datetime import date

import numpy as np

from surveillance.generator.market import (
    EXCHANGE_TZ,
    MINUTES_PER_SESSION,
    build_market,
    trading_days,
)
from surveillance.generator.reference import build_securities


def test_trading_days_skip_weekends():
    days = trading_days(date(2025, 3, 3), 20)
    assert len(days) == 20
    assert all(d.weekday() < 5 for d in days)
    assert days[0] == date(2025, 3, 3)


def test_session_times_respect_dst_transition():
    """US DST starts 2025-03-09, inside the simulated window. A fixed UTC offset would
    silently shift every session after that date by an hour."""
    secs = build_securities(1, 5)
    market = build_market(1, secs, trading_days(date(2025, 3, 3), 20))
    before = market.timestamp(0, 0)
    after = market.timestamp(19, 0)
    assert before.astimezone(EXCHANGE_TZ).hour == 9
    assert after.astimezone(EXCHANGE_TZ).hour == 9
    assert before.utcoffset() != after.utcoffset()


def test_prices_are_positive_and_finite():
    secs = build_securities(7, 30)
    market = build_market(7, secs, trading_days(n_days=10))
    assert market.mids.shape == (30, 10 * MINUTES_PER_SESSION)
    assert np.isfinite(market.mids).all()
    assert (market.mids > 0).all()


def test_realised_volatility_tracks_the_target():
    """annual_vol must mean what it says. The intraday smile and the overnight gap both
    add variance; if they are not normalised out, every security is ~40% more volatile
    than configured and the price-outlier scenarios become far easier than intended."""
    secs = build_securities(11, 40)
    market = build_market(11, secs, trading_days(n_days=20))
    ratios = []
    for sec in secs:
        returns = np.diff(np.log(market.mid_series(sec.id)))
        realised = returns.std() * np.sqrt(252 * MINUTES_PER_SESSION)
        ratios.append(realised / sec.annual_vol)
    ratios = np.array(ratios)
    assert 0.9 < ratios.mean() < 1.1
    assert ratios.max() < 1.35


def test_execution_price_crosses_the_spread():
    secs = build_securities(3, 10)
    market = build_market(3, secs, trading_days(n_days=3))
    rng = np.random.default_rng(0)
    sec = secs[0]
    mid = market.mid_at(sec.id, 0, 100)
    buys = [market.execution_price(rng, sec.id, 0, 100, True) for _ in range(400)]
    sells = [market.execution_price(rng, sec.id, 0, 100, False) for _ in range(400)]
    assert np.mean(buys) > mid > np.mean(sells)
