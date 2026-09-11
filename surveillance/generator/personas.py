"""Per-account behavioural personas.

Every planted anomaly is defined *relative to the account's own persona*, which is what
makes the hard cases hard. A wash-ring trade is drawn from the ring member's own normal
notional distribution, so no per-trade feature can separate it from that account's ordinary
activity -- only the network structure can. Likewise the "legitimate large block" hard
negative is large in absolute terms but ordinary for an account whose persona is large.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from surveillance.db.enums import AccountType, LiquidityTier
from surveillance.generator.market import MINUTES_PER_SESSION
from surveillance.generator.reference import AccountRef, SecurityRef
from surveillance.generator.rng import rng_for

#: (median trades/day, lognormal sigma of the rate)
DAILY_RATE: dict[AccountType, tuple[float, float]] = {
    AccountType.RETAIL: (4.5, 0.75),
    AccountType.INSTITUTIONAL: (19.0, 0.55),
    AccountType.HEDGE_FUND: (38.0, 0.60),
    AccountType.PROP_DESK: (52.0, 0.55),
    AccountType.MARKET_MAKER: (115.0, 0.45),
}

#: (median notional, lognormal sigma)
NOTIONAL: dict[AccountType, tuple[float, float]] = {
    AccountType.RETAIL: (4_200.0, 0.85),
    AccountType.INSTITUTIONAL: (185_000.0, 0.95),
    AccountType.HEDGE_FUND: (92_000.0, 1.05),
    AccountType.PROP_DESK: (46_000.0, 0.95),
    # Widest dispersion of any type, not the narrowest: a market maker does not choose
    # its trade sizes, it fills whatever size the arriving client order specifies.
    AccountType.MARKET_MAKER: (11_500.0, 1.05),
}

#: (min, max) number of securities an account actually trades.
WATCHLIST_SIZE: dict[AccountType, tuple[int, int]] = {
    AccountType.RETAIL: (3, 9),
    AccountType.INSTITUTIONAL: (8, 20),
    AccountType.HEDGE_FUND: (10, 26),
    AccountType.PROP_DESK: (9, 30),
    AccountType.MARKET_MAKER: (4, 14),
}

#: How strongly an account prefers liquid names when choosing its watchlist.
TIER_APPETITE: dict[AccountType, dict[LiquidityTier, float]] = {
    AccountType.RETAIL: {
        LiquidityTier.LIQUID: 5.0, LiquidityTier.MID: 2.0, LiquidityTier.ILLIQUID: 0.5,
    },
    AccountType.INSTITUTIONAL: {
        LiquidityTier.LIQUID: 5.0, LiquidityTier.MID: 2.5, LiquidityTier.ILLIQUID: 0.4,
    },
    AccountType.HEDGE_FUND: {
        LiquidityTier.LIQUID: 3.0, LiquidityTier.MID: 3.0, LiquidityTier.ILLIQUID: 1.8,
    },
    AccountType.PROP_DESK: {
        LiquidityTier.LIQUID: 3.0, LiquidityTier.MID: 3.0, LiquidityTier.ILLIQUID: 2.2,
    },
    AccountType.MARKET_MAKER: {
        LiquidityTier.LIQUID: 4.0, LiquidityTier.MID: 3.5, LiquidityTier.ILLIQUID: 1.5,
    },
}

#: Share of accounts that trade only within a narrow window of the session rather than
#: across the whole day. Real retail flow is full of these -- people trade before work, at
#: lunch, or on the close -- and institutional desks often have a mandated execution
#: window. It also makes "out-of-pattern timing" a meaningful concept: for an account
#: active all session, no time of day is genuinely surprising, so there is nothing for a
#: timing feature to detect.
CONCENTRATED_SCHEDULE_RATE: dict[AccountType, float] = {
    AccountType.RETAIL: 0.45,
    AccountType.INSTITUTIONAL: 0.25,
    AccountType.HEDGE_FUND: 0.10,
    AccountType.PROP_DESK: 0.10,
    AccountType.MARKET_MAKER: 0.0,
}

#: How much of its activity a concentrated account places outside its window. Not zero:
#: people do occasionally trade off-schedule, and a detector that fires on a hard zero is
#: detecting a generator artefact rather than a behaviour.
OFF_WINDOW_LEAKAGE = 0.04

#: Monday..Friday activity multipliers.
DOW_MULTIPLIER = np.array([1.06, 1.02, 1.00, 1.01, 0.94])


def base_intraday_intensity() -> np.ndarray:
    """The classic U-shaped volume curve: heavy at the open, heavy into the close."""
    u = np.linspace(0.0, 1.0, MINUTES_PER_SESSION, endpoint=False)
    return 0.30 + 1.00 * np.exp(-u / 0.10) + 0.55 * np.exp(-(1.0 - u) / 0.08)


@dataclass(slots=True)
class Persona:
    account_id: int
    account_type: AccountType
    daily_rate: float
    log_notional_mu: float
    log_notional_sigma: float
    watchlist: np.ndarray  # security ids
    watch_weights: np.ndarray  # probability per watchlist entry
    buy_prob: float
    #: Per-minute probability distribution over the session (sums to 1).
    minute_pmf: np.ndarray
    two_sided: bool
    #: True when this account confines its trading to a narrow window of the session.
    concentrated: bool = False

    def median_notional(self) -> float:
        return float(np.exp(self.log_notional_mu))

    def draw_notional(self, rng: np.random.Generator, size: int = 1) -> np.ndarray:
        return np.exp(rng.normal(self.log_notional_mu, self.log_notional_sigma, size))


def build_personas(
    master_seed: int, accounts: list[AccountRef], securities: list[SecurityRef]
) -> dict[int, Persona]:
    rng = rng_for(master_seed, "personas")
    base = base_intraday_intensity()
    u = np.linspace(0.0, 1.0, MINUTES_PER_SESSION, endpoint=False)
    sec_ids = np.array([s.id for s in securities])
    sec_tiers = [s.liquidity_tier for s in securities]

    personas: dict[int, Persona] = {}
    for acct in accounts:
        atype = acct.account_type
        rate_med, rate_sig = DAILY_RATE[atype]
        notional_med, notional_sig = NOTIONAL[atype]

        appetite = np.array([TIER_APPETITE[atype][t] for t in sec_tiers], dtype=float)
        appetite /= appetite.sum()
        lo, hi = WATCHLIST_SIZE[atype]
        # Clamp to the universe: prop desks watch up to 30 names, which exceeds the
        # security count in the small configurations used by tests.
        hi = min(hi, len(sec_ids))
        lo = min(lo, hi)
        k = int(rng.integers(lo, hi + 1))
        picks = rng.choice(len(sec_ids), size=k, replace=False, p=appetite)
        weights = rng.dirichlet(np.full(k, 0.9))

        # Each account tilts the market-wide U-curve towards the open or the close. This
        # gives the statistical layer a genuine per-account time-of-day profile to learn,
        # rather than one global curve that would make the 'odd hour' feature trivial.
        tilt = float(rng.normal(0.0, 0.9))
        pmf = base * np.exp(tilt * (0.5 - u))

        concentrated = bool(rng.random() < CONCENTRATED_SCHEDULE_RATE[atype])
        if concentrated:
            width = int(rng.integers(60, 181))  # a one- to three-hour habit
            start = int(rng.integers(0, MINUTES_PER_SESSION - width))
            mask = np.full(MINUTES_PER_SESSION, OFF_WINDOW_LEAKAGE)
            mask[start : start + width] = 1.0
            pmf = pmf * mask

        pmf /= pmf.sum()

        personas[acct.id] = Persona(
            account_id=acct.id,
            account_type=atype,
            daily_rate=float(rate_med * np.exp(rng.normal(0.0, rate_sig))),
            log_notional_mu=float(np.log(notional_med) + rng.normal(0.0, 0.45)),
            log_notional_sigma=float(notional_sig * rng.uniform(0.8, 1.2)),
            watchlist=sec_ids[picks],
            watch_weights=weights,
            buy_prob=float(np.clip(rng.normal(0.5, 0.07), 0.28, 0.72)),
            minute_pmf=pmf,
            two_sided=atype is AccountType.MARKET_MAKER,
            concentrated=concentrated,
        )
    return personas
