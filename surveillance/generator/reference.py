"""The static universe: accounts and securities.

Kept separate from activity generation because reference data is a slowly-changing
dimension -- the personas and price paths are derived from it, not the other way round.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from surveillance.db.enums import AccountType, LiquidityTier, RiskTier
from surveillance.generator.names import account_name, company_name, ticker_from
from surveillance.generator.rng import rng_for

SECTORS = [
    "Technology",
    "Financials",
    "Healthcare",
    "Energy",
    "Industrials",
    "Consumer",
    "Materials",
    "Utilities",
    "Real Estate",
    "Communications",
]

# Population mix. Market makers are few but generate a large share of trades -- which is
# exactly why one of them makes a good hard negative for the wash-trade rule.
ACCOUNT_MIX: dict[AccountType, float] = {
    AccountType.RETAIL: 0.55,
    AccountType.INSTITUTIONAL: 0.20,
    AccountType.HEDGE_FUND: 0.12,
    AccountType.PROP_DESK: 0.09,
    AccountType.MARKET_MAKER: 0.04,
}

RISK_TIER_WEIGHTS: dict[AccountType, tuple[float, float, float]] = {
    # (low, medium, high)
    AccountType.RETAIL: (0.70, 0.25, 0.05),
    AccountType.INSTITUTIONAL: (0.55, 0.35, 0.10),
    AccountType.HEDGE_FUND: (0.25, 0.45, 0.30),
    AccountType.PROP_DESK: (0.30, 0.45, 0.25),
    AccountType.MARKET_MAKER: (0.40, 0.45, 0.15),
}

LIQUIDITY_MIX: dict[LiquidityTier, float] = {
    LiquidityTier.LIQUID: 0.25,
    LiquidityTier.MID: 0.42,
    LiquidityTier.ILLIQUID: 0.33,
}

#: (min_adv, max_adv, half_spread_bps, annualised_vol)
LIQUIDITY_PARAMS: dict[LiquidityTier, tuple[float, float, float, float]] = {
    LiquidityTier.LIQUID: (8e6, 9e7, 1.0, 0.22),
    LiquidityTier.MID: (4e5, 8e6, 6.0, 0.34),
    LiquidityTier.ILLIQUID: (1e4, 4e5, 28.0, 0.55),
}

@dataclass(frozen=True, slots=True)
class SecurityRef:
    id: int
    ticker: str
    name: str
    sector: str
    liquidity_tier: LiquidityTier
    adv: float
    reference_price: float
    half_spread_bps: float
    annual_vol: float

    def to_row(self) -> dict:
        d = asdict(self)
        d["liquidity_tier"] = self.liquidity_tier.value
        return d


@dataclass(frozen=True, slots=True)
class AccountRef:
    id: int
    external_ref: str
    name: str
    account_type: AccountType
    risk_tier: RiskTier

    def to_row(self) -> dict:
        d = asdict(self)
        d["account_type"] = self.account_type.value
        d["risk_tier"] = self.risk_tier.value
        return d


def build_securities(master_seed: int, n_securities: int = 60) -> list[SecurityRef]:
    rng = rng_for(master_seed, "reference.securities")
    tiers = list(LIQUIDITY_MIX)
    probs = np.array([LIQUIDITY_MIX[t] for t in tiers], dtype=float)
    probs /= probs.sum()
    # Fixed counts rather than a multinomial draw: the detection layer's behaviour depends
    # on how many illiquid names exist, so that count should not wobble with the seed.
    counts = np.floor(probs * n_securities).astype(int)
    counts[0] += n_securities - counts.sum()

    used_names: set[str] = set()
    used_tickers: set[str] = set()
    out: list[SecurityRef] = []
    sec_id = 1
    for tier, count in zip(tiers, counts, strict=True):
        adv_lo, adv_hi, spread, vol = LIQUIDITY_PARAMS[tier]
        for _ in range(count):
            sector = str(rng.choice(SECTORS))
            name = company_name(rng, sector, used_names)
            ticker = ticker_from(name, used_tickers)
            adv = float(np.exp(rng.uniform(np.log(adv_lo), np.log(adv_hi))))
            price = float(np.exp(rng.uniform(np.log(6.0), np.log(420.0))))
            out.append(
                SecurityRef(
                    id=sec_id,
                    ticker=ticker,
                    name=name,
                    sector=sector,
                    liquidity_tier=tier,
                    adv=round(adv, 2),
                    reference_price=round(price, 4),
                    half_spread_bps=spread * float(rng.uniform(0.75, 1.35)),
                    annual_vol=vol * float(rng.uniform(0.8, 1.25)),
                )
            )
            sec_id += 1
    out.sort(key=lambda s: s.id)
    return out


def build_accounts(master_seed: int, n_accounts: int = 350) -> list[AccountRef]:
    rng = rng_for(master_seed, "reference.accounts")
    types = list(ACCOUNT_MIX)
    probs = np.array([ACCOUNT_MIX[t] for t in types], dtype=float)
    probs /= probs.sum()
    counts = np.floor(probs * n_accounts).astype(int)
    counts[0] += n_accounts - counts.sum()

    used_names: set[str] = set()
    out: list[AccountRef] = []
    acct_id = 1
    for atype, count in zip(types, counts, strict=True):
        w = np.array(RISK_TIER_WEIGHTS[atype], dtype=float)
        for _ in range(count):
            tier = RiskTier(["low", "medium", "high"][int(rng.choice(3, p=w))])
            out.append(
                AccountRef(
                    id=acct_id,
                    external_ref=f"ACC-{acct_id:05d}",
                    name=account_name(rng, atype, used_names),
                    account_type=atype,
                    risk_tier=tier,
                )
            )
            acct_id += 1
    out.sort(key=lambda a: a.id)
    return out
