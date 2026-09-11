"""Planted wash-trading rings -- the class the graph layer exists for.

The defining property, and the whole reason this project has two detection layers:

    **No individual trade in a ring is statistically unusual.**

Quantities are drawn near the *shared* median notional of the ring members, so every trade
sits inside its own account's ordinary size distribution. Timing is inside normal session
hours. Prices are at the prevailing mid. A per-trade scorer looking at size, price
deviation and time-of-day has nothing to grab onto -- by construction, not by accident.

What *is* anomalous is only visible as structure over many trades: a small closed set of
accounts passing the same position around a thinly-traded name, with each member ending
flat. That is a property of the graph, not of any row in it.

Ring members are chosen from a narrow median-notional band so that one quantity can be
normal for every member simultaneously. That is also how a real ring works -- an operator
sizes the accounts they control alike.
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

N_RINGS = 6
#: Rings are run by controlled accounts, not by market makers (whose two-sided flow is
#: legitimate and is planted separately as a hard negative).
ELIGIBLE = (
    AccountType.RETAIL,
    AccountType.PROP_DESK,
    AccountType.HEDGE_FUND,
    AccountType.INSTITUTIONAL,
)


def _pick_band(rng: np.random.Generator, ctx: ScenarioContext) -> tuple[float, float]:
    """A narrow notional band containing enough eligible accounts to form a ring."""
    medians = np.array(
        [
            p.median_notional()
            for aid, p in ctx.personas.items()
            if p.account_type in ELIGIBLE and aid not in ctx.reserved_accounts
        ]
    )
    centre = float(np.exp(rng.uniform(np.log(np.percentile(medians, 20)),
                                      np.log(np.percentile(medians, 80)))))
    return centre / 2.2, centre * 2.2


def generate(rng: np.random.Generator, ctx: ScenarioContext) -> ScenarioOutput:
    out = ScenarioOutput()
    n_days = len(ctx.market.days)
    # Rings hide in names with little natural volume; a wash trade in a mega-cap would be
    # lost in real flow and would also be pointless for the manipulator.
    candidate_secs = [
        s for s in ctx.securities_in_tier(LiquidityTier.ILLIQUID)
        + ctx.securities_in_tier(LiquidityTier.MID)
        if s not in ctx.reserved_securities
    ]

    for i in range(1, ctx.scaled(N_RINGS) + 1):
        scenario_id = f"wash_ring_{i:02d}"
        lo, hi = _pick_band(rng, ctx)
        n_members = int(rng.integers(3, 6))
        if not candidate_secs:
            break
        members = ctx.available_accounts(
            rng, n_members, types=ELIGIBLE, notional_band=(lo, hi)
        )
        ctx.reserve(members)

        sec_id = int(rng.choice(candidate_secs))
        ctx.reserved_securities.add(sec_id)
        candidate_secs = [s for s in candidate_secs if s != sec_id]

        # The ring's working size: near every member's own median, so no trade is a size
        # outlier for anyone involved.
        member_medians = np.array([ctx.personas[m].median_notional() for m in members])
        ring_notional = float(np.exp(np.mean(np.log(member_medians))))

        n_rounds = int(rng.integers(6, 15))
        span_days = int(rng.integers(1, 4))
        start_day = int(rng.integers(1, max(2, n_days - span_days)))

        minute = clamp_minute(rng.integers(30, 200))
        day_idx = start_day
        first_ts = ctx.timestamp(day_idx, minute, 0)
        last_ts = first_ts

        for _round in range(n_rounds):
            # A closed cycle: each member passes the position to the next, and the last
            # passes it back to the first. Every member nets to flat.
            order = list(rng.permutation(members))
            round_qty_notional = ring_notional * float(rng.uniform(0.94, 1.06))
            for k in range(len(order)):
                seller = int(order[k])
                buyer = int(order[(k + 1) % len(order)])
                price = ctx.market.mid_at(sec_id, day_idx, minute)
                # Traded essentially at mid: no price-outlier signal either.
                price *= 1.0 + float(rng.normal(0.0, 0.0004))
                qty = qty_for_notional(round_qty_notional, price)
                out.add_trade(
                    account_id=seller, security_id=sec_id, is_buy=False, quantity=qty,
                    price=price, day_idx=day_idx, minute=minute,
                    second=int(rng.integers(0, 60)), scenario_id=scenario_id,
                    counterparty_account_id=buyer,
                )
                out.add_trade(
                    account_id=buyer, security_id=sec_id, is_buy=True, quantity=qty,
                    price=price, day_idx=day_idx, minute=minute,
                    second=int(rng.integers(0, 60)), scenario_id=scenario_id,
                    counterparty_account_id=seller,
                )
                last_ts = ctx.timestamp(day_idx, minute, 59)
                # Gaps of 30s-5min between legs; roll into the next day when the session ends.
                minute = minute + int(rng.integers(1, 6))
                if minute >= 380:
                    day_idx = min(day_idx + 1, n_days - 1)
                    minute = clamp_minute(rng.integers(30, 200))

        out.labels.append(
            ScenarioLabel(
                scenario_id=scenario_id,
                title=(
                    f"Circular trading between {n_members} accounts in {ctx.ticker(sec_id)}"
                ),
                case_ref="",
                scenario_type="wash_ring",
                subtype="wash_ring",
                label="positive",
                expected_layer="graph",
                account_ids=sorted(int(m) for m in members),
                security_ids=[sec_id],
                window_start=first_ts,
                window_end=last_ts,
                difficulty="hard",
                notes=(
                    f"{n_members}-account closed cycle, {n_rounds} rounds in one name; "
                    "every member nets flat and every individual trade is inside that "
                    "account's normal size distribution (per-trade scorer should miss it)"
                ),
            )
        )
    return out
