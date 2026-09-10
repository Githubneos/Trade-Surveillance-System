"""Planted hard negatives: legitimate activity deliberately shaped to look abusive.

Every one of these targets a specific, nameable way a detector cheats. Without them a
surveillance system can post excellent recall while being useless, because the thing that
kills a real compliance team is alert volume, not missed cases. These are scored and
reported separately, by name -- a hard negative that fires is called out, not averaged away.

  (a) liquid_crowding    -> kills a co-trading graph that ignores base rates. Dozens of
                            accounts in one mega-cap in one hour is a dense clique, and
                            entirely normal. Only an edge weight measured against
                            *expected* co-trading can tell it apart from a ring.
  (b) mm_two_sided       -> kills a wash rule that keys on "lots of back-and-forth".
                            A market maker's flow is bidirectional by design, but its
                            counterparties are many and varied and it ends net directional.
  (c) legit_block        -> kills a scorer using absolute rather than account-relative
                            size. Millions of dollars, ordinary for this account.
  (d) event_comovement   -> kills a coordination rule that keys only on "many accounts,
                            one name, tight window". Scheduled public news does that too,
                            but in a liquid name, with two-way flow, among holders.
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

N_LIQUID_CROWDING = 3
N_MM_TWO_SIDED = 3
N_LEGIT_BLOCK = 6
N_EVENT_COMOVEMENT = 3

ORDINARY = (
    AccountType.RETAIL,
    AccountType.INSTITUTIONAL,
    AccountType.HEDGE_FUND,
    AccountType.PROP_DESK,
)


def _most_liquid(ctx: ScenarioContext, exclude: set[int]) -> list[int]:
    secs = [
        s for s in ctx.securities.values()
        if s.liquidity_tier is LiquidityTier.LIQUID and s.id not in exclude
    ]
    secs.sort(key=lambda s: -s.adv)
    return [s.id for s in secs]


def _liquid_crowding(rng: np.random.Generator, ctx: ScenarioContext, out: ScenarioOutput) -> None:
    pool = _most_liquid(ctx, ctx.reserved_securities)
    n_days = len(ctx.market.days)
    for i in range(1, min(ctx.scaled(N_LIQUID_CROWDING), len(pool)) + 1):
        scenario_id = f"hn_liquid_crowding_{i:02d}"
        sec_id = pool[i - 1]
        n_accounts = int(rng.integers(22, 31))
        members = ctx.available_accounts(rng, n_accounts, types=ORDINARY)
        # NOT reserved: these accounts are behaving normally and may legitimately appear
        # elsewhere. Reserving them would be over-claiming.
        day_idx = int(rng.integers(1, n_days))
        start_minute = clamp_minute(rng.integers(30, 300))
        minutes = []
        for m in members:
            persona = ctx.personas[m]
            for _ in range(int(rng.integers(1, 4))):
                minute = clamp_minute(start_minute + rng.integers(0, 61))
                is_buy = bool(rng.random() < persona.buy_prob)
                price = ctx.market.execution_price(rng, sec_id, day_idx, int(minute), is_buy)
                qty = qty_for_notional(persona.draw_notional(rng, 1)[0], price)
                out.add_trade(
                    account_id=m, security_id=sec_id, is_buy=is_buy, quantity=qty, price=price,
                    day_idx=day_idx, minute=int(minute), second=int(rng.integers(0, 60)),
                    scenario_id=scenario_id,
                )
                minutes.append(int(minute))
        out.labels.append(
            ScenarioLabel(
                scenario_id=scenario_id, scenario_type="liquid_crowding", subtype="liquid_crowding",
                label="hard_negative", expected_layer="none",
                account_ids=sorted(int(m) for m in members), security_ids=[sec_id],
                window_start=ctx.timestamp(day_idx, min(minutes), 0),
                window_end=ctx.timestamp(day_idx, max(minutes), 59),
                difficulty="hard",
                notes=(
                    f"{n_accounts} unrelated accounts trading the same mega-cap "
                    "within an "
                    "hour, normal sizes, two-way flow -- a dense clique that is entirely "
                    "ordinary. Tests whether graph edge weights account for base rates."
                ),
            )
        )


def _mm_two_sided(rng: np.random.Generator, ctx: ScenarioContext, out: ScenarioOutput) -> None:
    n_days = len(ctx.market.days)
    mms = [aid for aid, p in ctx.personas.items() if p.account_type is AccountType.MARKET_MAKER]
    mid_pool = [s for s in ctx.securities_in_tier(LiquidityTier.MID)
                if s not in ctx.reserved_securities]
    n_mm = min(ctx.scaled(N_MM_TWO_SIDED), len(mms), len(mid_pool))
    chosen = rng.choice(np.array(mms), size=n_mm, replace=False)

    for i, mm_id in enumerate(chosen, start=1):
        scenario_id = f"hn_mm_two_sided_{i:02d}"
        mm_id = int(mm_id)
        sec_id = int(mid_pool[i - 1])
        ctx.reserved_securities.add(sec_id)
        persona = ctx.personas[mm_id]
        # Clients of comparable size. The band is deliberately tight: fills are sized by
        # the client, so a wide band would push the market maker's own trades outside its
        # own normal size range and trip the statistical layer. This scenario exists to
        # test the GRAPH layer's wash-trade rule, so it must not smuggle in a size anomaly.
        clients = ctx.available_accounts(
            rng, int(rng.integers(6, 11)), types=ORDINARY,
            notional_band=(persona.median_notional() / 2.5, persona.median_notional() * 2.5),
        )
        day_idx = int(rng.integers(1, n_days))

        n_fills = int(rng.integers(70, 131))
        # The discriminator against a wash ring: the market maker takes the other side of
        # many *different* clients and ends the day net directional, rather than a closed
        # cycle of the same few accounts all netting flat.
        client_buy_bias = 0.66
        minute = clamp_minute(rng.integers(20, 120))
        minutes = []
        for _ in range(n_fills):
            client = int(rng.choice(clients))
            client_buys = bool(rng.random() < client_buy_bias)
            price = ctx.market.execution_price(rng, sec_id, day_idx, int(minute), client_buys)
            # Order size is set by the CLIENT, not the market maker. Sizing these fills from
            # the MM's own distribution would make every fill a size outlier for whichever
            # client happened to be on the other side -- the hard negative would then trip
            # the statistical layer for a reason that has nothing to do with what it tests.
            cp = ctx.personas[client]
            notional = float(np.exp(rng.normal(cp.log_notional_mu, cp.log_notional_sigma)))
            qty = qty_for_notional(notional, price)
            out.add_trade(
                account_id=mm_id, security_id=sec_id, is_buy=not client_buys, quantity=qty,
                price=price, day_idx=day_idx, minute=int(minute),
                second=int(rng.integers(0, 60)), scenario_id=scenario_id,
                counterparty_account_id=client,
            )
            out.add_trade(
                account_id=client, security_id=sec_id, is_buy=client_buys, quantity=qty,
                price=price, day_idx=day_idx, minute=int(minute),
                second=int(rng.integers(0, 60)), scenario_id=scenario_id,
                counterparty_account_id=mm_id,
            )
            minutes.append(int(minute))
            minute = clamp_minute(minute + rng.integers(0, 4))

        out.labels.append(
            ScenarioLabel(
                scenario_id=scenario_id, scenario_type="mm_two_sided", subtype="mm_two_sided",
                label="hard_negative", expected_layer="none",
                account_ids=sorted([mm_id, *(int(c) for c in clients)]), security_ids=[sec_id],
                window_start=ctx.timestamp(day_idx, min(minutes), 0),
                window_end=ctx.timestamp(day_idx, max(minutes), 59),
                difficulty="hard",
                notes=(
                    f"market maker filling {len(clients)} clients {n_fills} times in one name; "
                    "heavy bidirectional flow that superficially resembles a wash ring, but "
                    "counterparties are varied and the MM ends net short, not flat"
                ),
            )
        )


def _legit_block(rng: np.random.Generator, ctx: ScenarioContext, out: ScenarioOutput) -> None:
    n_days = len(ctx.market.days)
    # Deliberately the largest accounts in the population, so the absolute notional is
    # eye-watering while the account-relative z-score is unremarkable.
    big = sorted(
        (aid for aid, p in ctx.personas.items()
         if p.account_type is AccountType.INSTITUTIONAL and aid not in ctx.reserved_accounts),
        key=lambda aid: -ctx.personas[aid].median_notional(),
    )[:20]
    chosen = rng.choice(np.array(big), size=min(ctx.scaled(N_LEGIT_BLOCK), len(big)), replace=False)

    for i, aid in enumerate(chosen, start=1):
        scenario_id = f"hn_legit_block_{i:02d}"
        aid = int(aid)
        ctx.reserve([aid])
        persona = ctx.personas[aid]
        sec_id = int(rng.choice(persona.watchlist, p=persona.watch_weights))
        day_idx = int(rng.integers(2, n_days))
        minute = int(rng.choice(len(persona.minute_pmf), p=persona.minute_pmf))
        is_buy = bool(rng.random() < persona.buy_prob)
        price = ctx.market.execution_price(rng, sec_id, day_idx, minute, is_buy)
        multiple = float(rng.uniform(2.5, 4.0))
        notional = persona.median_notional() * multiple
        qty = qty_for_notional(notional, price)
        out.add_trade(
            account_id=aid, security_id=sec_id, is_buy=is_buy, quantity=qty, price=price,
            day_idx=day_idx, minute=minute, second=int(rng.integers(0, 60)),
            scenario_id=scenario_id,
        )
        out.labels.append(
            ScenarioLabel(
                scenario_id=scenario_id, scenario_type="legit_block", subtype="legit_block",
                label="hard_negative", expected_layer="none",
                account_ids=[aid], security_ids=[sec_id],
                window_start=ctx.timestamp(day_idx, minute, 0),
                window_end=ctx.timestamp(day_idx, minute, 59),
                difficulty="medium",
                notes=(
                    f"${notional:,.0f} block -- {multiple:.1f}x this account's own median and "
                    "well inside its normal range. Tests that size features are "
                    "account-relative, not absolute."
                ),
            )
        )


def _event_comovement(rng: np.random.Generator, ctx: ScenarioContext, out: ScenarioOutput) -> None:
    n_days = len(ctx.market.days)
    pool = [s for s in ctx.securities_in_tier(LiquidityTier.MID)
            if s not in ctx.reserved_securities]
    for i in range(1, min(ctx.scaled(N_EVENT_COMOVEMENT), len(pool)) + 1):
        scenario_id = f"hn_event_comovement_{i:02d}"
        sec_id = int(pool[i - 1])
        ctx.reserved_securities.add(sec_id)
        # Holders of the name react to public news -- so participants are drawn from
        # accounts that already have this security on their watchlist.
        holders = [
            aid for aid, p in ctx.personas.items()
            if sec_id in set(p.watchlist.tolist()) and aid not in ctx.reserved_accounts
        ]
        if len(holders) < 12:
            holders = ctx.available_accounts(rng, 20, types=ORDINARY)
        n_accounts = min(len(holders), int(rng.integers(18, 29)))
        members = [int(x) for x in rng.choice(np.array(holders), size=n_accounts, replace=False)]

        day_idx = int(rng.integers(2, n_days))
        window_minutes = int(rng.integers(30, 51))
        start_minute = clamp_minute(rng.integers(20, 330))
        minutes = []
        for m in members:
            persona = ctx.personas[m]
            minute = clamp_minute(start_minute + rng.integers(0, window_minutes + 1))
            # Two-way flow: public news makes some holders buy and others take profit.
            is_buy = bool(rng.random() < 0.55)
            price = ctx.market.execution_price(rng, sec_id, day_idx, int(minute), is_buy)
            qty = qty_for_notional(persona.median_notional() * float(rng.uniform(1.5, 2.5)), price)
            out.add_trade(
                account_id=m, security_id=sec_id, is_buy=is_buy, quantity=qty, price=price,
                day_idx=day_idx, minute=int(minute), second=int(rng.integers(0, 60)),
                scenario_id=scenario_id,
            )
            minutes.append(int(minute))

        out.labels.append(
            ScenarioLabel(
                scenario_id=scenario_id, scenario_type="event_comovement",
                subtype="event_comovement", label="hard_negative", expected_layer="none",
                account_ids=sorted(members), security_ids=[sec_id],
                window_start=ctx.timestamp(day_idx, min(minutes), 0),
                window_end=ctx.timestamp(day_idx, max(minutes), 59),
                difficulty="hard",
                notes=(
                    f"{n_accounts} existing holders reacting to scheduled public news in a "
                    f"mid-liquid name inside {window_minutes} minutes, two-way flow at "
                    "1.5-2.5x normal size -- the closest legitimate analogue of the "
                    "coordinated-cluster positives"
                ),
            )
        )


def generate(rng: np.random.Generator, ctx: ScenarioContext) -> ScenarioOutput:
    out = ScenarioOutput()
    # Order matters: the scenarios that need specific securities or the largest accounts
    # claim them before the broader ones.
    _mm_two_sided(rng, ctx, out)
    _event_comovement(rng, ctx, out)
    _legit_block(rng, ctx, out)
    _liquid_crowding(rng, ctx, out)
    return out
