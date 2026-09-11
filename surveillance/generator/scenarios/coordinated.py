"""Planted coordinated clustering -- simulating information leakage.

A set of accounts with no prior relationship to each other, and no history in the name,
all take the *same side* of a thinly-traded security inside a tight window. Individually
each trade is only mildly large (1.5-3x the account's own median), so the statistical layer
may catch a few of the larger ones but should not catch the pattern; the pattern is the
simultaneity, which is a graph property.

Deliberately distinguished from the 'scheduled news co-movement' hard negative by three
things a real surveillance analyst would also use: the security is illiquid rather than
mid-liquid, the flow is directionally unanimous rather than two-way, and the participants
have no prior history in the name.
"""

from __future__ import annotations

import numpy as np

from surveillance.db.enums import AccountType, LiquidityTier
from surveillance.generator.ground_truth import ScenarioLabel
from surveillance.generator.scenarios.base import (
    ScenarioContext,
    ScenarioOutput,
    clamp_minute,
    qty_for_notional,
)

N_EVENTS = 5
ELIGIBLE = (
    AccountType.RETAIL,
    AccountType.INSTITUTIONAL,
    AccountType.HEDGE_FUND,
    AccountType.PROP_DESK,
)


def generate(rng: np.random.Generator, ctx: ScenarioContext) -> ScenarioOutput:
    out = ScenarioOutput()
    n_days = len(ctx.market.days)
    candidates = [s for s in ctx.securities_in_tier(LiquidityTier.ILLIQUID)
                  if s not in ctx.reserved_securities]

    for i in range(1, ctx.scaled(N_EVENTS) + 1):
        scenario_id = f"coordinated_{i:02d}"
        if not candidates:
            break
        n_accounts = int(rng.integers(8, 16))
        members = ctx.available_accounts(rng, n_accounts, types=ELIGIBLE)
        ctx.reserve(members)

        sec_id = int(rng.choice(candidates))
        ctx.reserved_securities.add(sec_id)
        candidates = [s for s in candidates if s != sec_id]

        day_idx = int(rng.integers(2, n_days))
        window_minutes = int(rng.integers(20, 46))
        start_minute = clamp_minute(rng.integers(20, 380 - window_minutes))
        is_buy = bool(rng.random() < 0.75)  # usually accumulation ahead of good news

        minutes = []
        for member in members:
            persona = ctx.personas[member]
            minute = clamp_minute(start_minute + rng.integers(0, window_minutes + 1))
            price = ctx.market.execution_price(rng, sec_id, day_idx, int(minute), is_buy)
            multiple = float(rng.uniform(1.5, 3.0))
            qty = qty_for_notional(persona.median_notional() * multiple, price)
            out.add_trade(
                account_id=member, security_id=sec_id, is_buy=is_buy, quantity=qty,
                price=price, day_idx=day_idx, minute=int(minute),
                second=int(rng.integers(0, 60)), scenario_id=scenario_id,
            )
            minutes.append(int(minute))

        out.labels.append(
            ScenarioLabel(
                scenario_id=scenario_id,
                title=(
                    f"Coordinated {'accumulation' if is_buy else 'disposal'} of "
                    f"{ctx.ticker(sec_id)} by {n_accounts} unrelated accounts"
                ),
                case_ref="",
                scenario_type="coordinated_cluster",
                subtype="coordinated_cluster",
                label="positive",
                expected_layer="graph",
                account_ids=sorted(int(m) for m in members),
                security_ids=[sec_id],
                window_start=ctx.timestamp(day_idx, min(minutes), 0),
                window_end=ctx.timestamp(day_idx, max(minutes), 59),
                difficulty="hard",
                notes=(
                    f"{n_accounts} unrelated accounts all {'buying' if is_buy else 'selling'} "
                    f"one illiquid name inside {window_minutes} minutes, at only 1.5-3x their "
                    "own median size"
                ),
            )
        )
    return out
