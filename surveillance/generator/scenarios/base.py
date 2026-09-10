"""Shared plumbing for planted scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from surveillance.db.enums import AccountType, LiquidityTier
from surveillance.generator.background import TRADE_FIELDS, _empty_records
from surveillance.generator.ground_truth import ScenarioLabel
from surveillance.generator.market import MINUTES_PER_SESSION, MarketData
from surveillance.generator.personas import Persona
from surveillance.generator.reference import SecurityRef


@dataclass(slots=True)
class ScenarioOutput:
    trades: dict[str, list] = field(default_factory=_empty_records)
    labels: list[ScenarioLabel] = field(default_factory=list)

    def add_trade(
        self,
        *,
        account_id: int,
        security_id: int,
        is_buy: bool,
        quantity: float,
        price: float,
        day_idx: int,
        minute: int,
        second: int,
        scenario_id: str,
        counterparty_account_id: int | None = None,
    ) -> None:
        self.trades["account_id"].append(int(account_id))
        self.trades["security_id"].append(int(security_id))
        self.trades["side"].append("buy" if is_buy else "sell")
        self.trades["quantity"].append(float(quantity))
        self.trades["price"].append(float(price))
        self.trades["day_idx"].append(int(day_idx))
        self.trades["minute"].append(int(minute))
        self.trades["second"].append(int(second))
        self.trades["counterparty_account_id"].append(counterparty_account_id)
        self.trades["scenario_id"].append(scenario_id)

    def merge(self, other: ScenarioOutput) -> None:
        for f in TRADE_FIELDS:
            self.trades[f].extend(other.trades[f])
        self.labels.extend(other.labels)


@dataclass(slots=True)
class ScenarioContext:
    """Everything a scenario needs, plus a reservation ledger.

    Accounts are reserved so that a single account is never a member of two different
    planted scenarios. Overlapping membership would make an alert ambiguous to score --
    it could be credited to the wrong scenario and quietly inflate recall.
    """

    personas: dict[int, Persona]
    securities: dict[int, SecurityRef]
    market: MarketData
    #: Scales planted scenario counts down for small universes (tests, quick iteration),
    #: so the generator degrades gracefully instead of exhausting the account pool.
    scale: float = 1.0
    reserved_accounts: set[int] = field(default_factory=set)
    reserved_securities: set[int] = field(default_factory=set)

    def scaled(self, n: int) -> int:
        return max(1, int(round(n * self.scale)))

    def securities_in_tier(self, tier: LiquidityTier) -> list[int]:
        return [s.id for s in self.securities.values() if s.liquidity_tier is tier]

    def available_accounts(
        self,
        rng: np.random.Generator,
        n: int,
        *,
        types: tuple[AccountType, ...] | None = None,
        notional_band: tuple[float, float] | None = None,
        max_daily_rate: float | None = None,
        exclude: set[int] | None = None,
    ) -> list[int]:
        pool = []
        blocked = self.reserved_accounts | (exclude or set())
        for aid, p in self.personas.items():
            if aid in blocked:
                continue
            if types and p.account_type not in types:
                continue
            if notional_band:
                med = p.median_notional()
                if not (notional_band[0] <= med <= notional_band[1]):
                    continue
            if max_daily_rate is not None and p.daily_rate > max_daily_rate:
                continue
            pool.append(aid)
        if len(pool) < n:
            raise ValueError(f"only {len(pool)} accounts available, needed {n}")
        picks = rng.choice(np.array(pool), size=n, replace=False)
        return [int(x) for x in picks]

    def reserve(self, account_ids: list[int]) -> None:
        self.reserved_accounts.update(int(a) for a in account_ids)

    def timestamp(self, day_idx: int, minute: int, second: int = 0):
        return self.market.timestamp(day_idx, minute, second)


def clamp_minute(minute: float) -> int:
    return int(np.clip(minute, 0, MINUTES_PER_SESSION - 1))


def qty_for_notional(notional: float, price: float) -> float:
    return float(max(1, int(round(notional / max(price, 0.01)))))
