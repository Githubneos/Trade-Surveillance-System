"""Trading calendar and synthetic price paths.

Prices are geometric Brownian motion on a one-minute grid with an overnight gap. That is a
deliberate simplification and a documented limitation: real prices have fat tails, jumps and
volatility clustering, none of which appear here. It matters for honesty about the
statistical layer -- a price-outlier detector has an easier job against GBM than against
real returns.

Session times use the America/New_York zone rather than a fixed UTC offset, so the DST
transition inside the simulated window lands on the correct wall-clock minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from surveillance.db.enums import LiquidityTier
from surveillance.generator.reference import SecurityRef
from surveillance.generator.rng import rng_for

EXCHANGE_TZ = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
MINUTES_PER_SESSION = 390  # 09:30 -> 16:00 inclusive of open, exclusive of close
MINUTES_PER_YEAR = 252 * MINUTES_PER_SESSION
#: Overnight gap variance as a multiple of one session's intraday variance.
GAP_VARIANCE_SHARE = 0.35

DEFAULT_START = date(2025, 3, 3)  # a Monday


def trading_days(start: date = DEFAULT_START, n_days: int = 20) -> list[date]:
    """Weekdays only. Exchange holidays are ignored -- a documented simplification that
    does not affect any detector, since all windows are relative to observed trade times."""
    days: list[date] = []
    d = start
    while len(days) < n_days:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def session_start(day: date) -> datetime:
    return datetime.combine(day, SESSION_OPEN, tzinfo=EXCHANGE_TZ)


@dataclass(slots=True)
class MarketData:
    """Minute-resolution mid prices for every security across the simulated window."""

    days: list[date]
    security_ids: list[int]
    #: shape (n_securities, n_days * MINUTES_PER_SESSION)
    mids: np.ndarray
    half_spread_bps: np.ndarray
    _index: dict[int, int]

    @property
    def n_minutes(self) -> int:
        return len(self.days) * MINUTES_PER_SESSION

    def row(self, security_id: int) -> int:
        return self._index[security_id]

    def mid_at(self, security_id: int, day_idx: int, minute_of_day: int) -> float:
        idx = day_idx * MINUTES_PER_SESSION + minute_of_day
        return float(self.mids[self._index[security_id], idx])

    def mid_series(self, security_id: int) -> np.ndarray:
        return self.mids[self._index[security_id]]

    def timestamp(self, day_idx: int, minute_of_day: int, second: int = 0) -> datetime:
        return session_start(self.days[day_idx]) + timedelta(
            minutes=int(minute_of_day), seconds=int(second)
        )

    def execution_price(
        self,
        rng: np.random.Generator,
        security_id: int,
        day_idx: int,
        minute_of_day: int,
        is_buy: bool,
    ) -> float:
        """Cross the spread in the direction of the aggressor, plus a little slippage."""
        mid = self.mid_at(security_id, day_idx, minute_of_day)
        half = self.half_spread_bps[self._index[security_id]] / 1e4
        signed = half if is_buy else -half
        slippage = float(rng.normal(0.0, half * 0.35))
        return max(0.01, mid * (1.0 + signed + slippage))


def build_market(
    master_seed: int, securities: list[SecurityRef], days: list[date]
) -> MarketData:
    rng = rng_for(master_seed, "market.prices")
    n_sec, n_days = len(securities), len(days)
    total = n_days * MINUTES_PER_SESSION

    mids = np.empty((n_sec, total), dtype=np.float64)
    spreads = np.array([s.half_spread_bps for s in securities], dtype=np.float64)

    # Mild intraday volatility smile: the open and close are noisier than midday. It is
    # normalised to unit mean-square so it redistributes variance across the session
    # without inflating the total -- sec.annual_vol stays the realised annualised vol.
    u = np.linspace(0.0, 1.0, MINUTES_PER_SESSION, endpoint=False)
    smile = 0.75 + 1.4 * np.exp(-u / 0.08) + 0.9 * np.exp(-(1.0 - u) / 0.06)
    smile /= np.sqrt(np.mean(smile**2))
    smile_full = np.tile(smile, n_days)

    for i, sec in enumerate(securities):
        # Total variance is split between intraday diffusion and overnight gaps, with gaps
        # carrying GAP_VARIANCE_SHARE of a session's variance. Solving for the intraday
        # per-minute sigma keeps total realised vol equal to sec.annual_vol.
        sigma_min = sec.annual_vol / np.sqrt(MINUTES_PER_YEAR * (1.0 + GAP_VARIANCE_SHARE))
        gap_sigma = sigma_min * np.sqrt(MINUTES_PER_SESSION * GAP_VARIANCE_SHARE)

        shocks = rng.normal(0.0, 1.0, total) * sigma_min * smile_full
        gap_idx = np.arange(1, n_days) * MINUTES_PER_SESSION
        shocks[gap_idx] += rng.normal(0.0, gap_sigma, n_days - 1)

        drift = -0.5 * (sigma_min * smile_full) ** 2
        log_path = np.log(sec.reference_price) + np.cumsum(drift + shocks)
        mids[i] = np.exp(log_path)

    return MarketData(
        days=days,
        security_ids=[s.id for s in securities],
        mids=mids,
        half_spread_bps=spreads,
        _index={s.id: i for i, s in enumerate(securities)},
    )


def liquidity_of(securities: list[SecurityRef]) -> dict[int, LiquidityTier]:
    return {s.id: s.liquidity_tier for s in securities}
