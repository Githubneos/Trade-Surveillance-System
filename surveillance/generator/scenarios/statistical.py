"""Planted point anomalies -- the class the per-trade statistical scorer should catch.

Four sub-variants, each stressing a different engineered feature. All are defined relative
to the account's own persona, never in absolute terms: a $2m trade is an outlier for a
retail account and a Tuesday for a pension fund.
"""

from __future__ import annotations

import numpy as np

from surveillance.db.enums import AccountType
from surveillance.generator.ground_truth import ScenarioLabel
from surveillance.generator.scenarios.base import (
    ScenarioContext,
    ScenarioOutput,
    clamp_minute,
    qty_for_notional,
)

#: Account types eligible for point anomalies. Market makers are excluded: their flow is so
#: high-rate and uniform that a single odd trade is not a meaningful surveillance concept.
ELIGIBLE = (
    AccountType.RETAIL,
    AccountType.INSTITUTIONAL,
    AccountType.HEDGE_FUND,
    AccountType.PROP_DESK,
)

VARIANTS = ("size_spike", "price_outlier", "odd_hour", "frequency_burst")
#: How many of each variant to plant.
VARIANT_COUNTS = {"size_spike": 14, "price_outlier": 10, "odd_hour": 8, "frequency_burst": 8}


def _pick_security(rng: np.random.Generator, ctx: ScenarioContext, account_id: int) -> int:
    p = ctx.personas[account_id]
    return int(rng.choice(p.watchlist, p=p.watch_weights))


def _low_probability_minute(rng: np.random.Generator, pmf: np.ndarray) -> int:
    """A minute this account almost never trades in -- bottom decile of its own profile."""
    order = np.argsort(pmf)
    tail = order[: max(1, len(order) // 10)]
    return int(rng.choice(tail))


def generate(rng: np.random.Generator, ctx: ScenarioContext) -> ScenarioOutput:
    out = ScenarioOutput()
    n_days = len(ctx.market.days)
    counter = 0

    for variant in VARIANTS:
        for _ in range(ctx.scaled(VARIANT_COUNTS[variant])):
            counter += 1
            scenario_id = f"stat_{variant}_{counter:03d}"
            if variant == "frequency_burst":
                # A 12-trade burst is only anomalous for an account that does not normally
                # trade 12 times. Planting one on a prop desk averaging 60 trades/day would
                # be an unlabelled false positive waiting to happen.
                account_id = ctx.available_accounts(
                    rng, 1, types=ELIGIBLE, max_daily_rate=10.0
                )[0]
            else:
                account_id = ctx.available_accounts(rng, 1, types=ELIGIBLE)[0]
            ctx.reserve([account_id])
            persona = ctx.personas[account_id]
            sec_id = _pick_security(rng, ctx, account_id)
            # Avoid the first day: the statistical layer needs some prior history for this
            # account before a "relative to its own past" feature means anything.
            day_idx = int(rng.integers(3, n_days))

            if variant == "size_spike":
                minute = int(rng.choice(len(persona.minute_pmf), p=persona.minute_pmf))
                is_buy = bool(rng.random() < persona.buy_prob)
                price = ctx.market.execution_price(rng, sec_id, day_idx, minute, is_buy)
                # Sized in the account's OWN z-space rather than as a raw multiple.
                # A "10x median" trade is a screaming outlier for a tight retail account
                # and unremarkable for a dispersed hedge fund, so a fixed multiple gives
                # wildly inconsistent difficulty. Targeting z directly makes the planted
                # severity mean the same thing for every account.
                target_z = float(rng.uniform(3.5, 6.5))
                notional = float(np.exp(
                    persona.log_notional_mu + target_z * persona.log_notional_sigma
                ))
                multiple = notional / persona.median_notional()
                qty = qty_for_notional(notional, price)
                out.add_trade(
                    account_id=account_id, security_id=sec_id, is_buy=is_buy, quantity=qty,
                    price=price, day_idx=day_idx, minute=minute, second=int(rng.integers(0, 60)),
                    scenario_id=scenario_id,
                )
                notes = (
                    f"single trade at z={target_z:.1f} in the account's own log-notional "
                    f"distribution ({multiple:.0f}x its median)"
                )
                window = (minute, minute)

            elif variant == "price_outlier":
                minute = int(rng.choice(len(persona.minute_pmf), p=persona.minute_pmf))
                is_buy = bool(rng.random() < persona.buy_prob)
                mid = ctx.market.mid_at(sec_id, day_idx, minute)
                # Executed well away from the prevailing mid, in the direction that is
                # unfavourable to the account -- the classic off-market-price pattern.
                dev = float(rng.uniform(0.03, 0.085)) * (1 if is_buy else -1)
                price = mid * (1.0 + dev)
                qty = qty_for_notional(persona.draw_notional(rng, 1)[0], price)
                out.add_trade(
                    account_id=account_id, security_id=sec_id, is_buy=is_buy, quantity=qty,
                    price=price, day_idx=day_idx, minute=minute, second=int(rng.integers(0, 60)),
                    scenario_id=scenario_id,
                )
                notes = f"executed {abs(dev)*100:.1f}% away from the prevailing mid"
                window = (minute, minute)

            elif variant == "odd_hour":
                minute = _low_probability_minute(rng, persona.minute_pmf)
                is_buy = bool(rng.random() < persona.buy_prob)
                price = ctx.market.execution_price(rng, sec_id, day_idx, minute, is_buy)
                qty = qty_for_notional(persona.draw_notional(rng, 1)[0] * 1.4, price)
                out.add_trade(
                    account_id=account_id, security_id=sec_id, is_buy=is_buy, quantity=qty,
                    price=price, day_idx=day_idx, minute=minute, second=int(rng.integers(0, 60)),
                    scenario_id=scenario_id,
                )
                notes = "trade placed in the bottom decile of this account's time-of-day profile"
                window = (minute, minute)

            else:  # frequency_burst
                n_trades = int(rng.integers(12, 22))
                start = clamp_minute(rng.integers(20, 360))
                minutes = sorted(clamp_minute(start + m) for m in rng.integers(0, 11, n_trades))
                for m in minutes:
                    is_buy = bool(rng.random() < persona.buy_prob)
                    price = ctx.market.execution_price(rng, sec_id, day_idx, m, is_buy)
                    qty = qty_for_notional(persona.draw_notional(rng, 1)[0], price)
                    out.add_trade(
                        account_id=account_id, security_id=sec_id, is_buy=is_buy, quantity=qty,
                        price=price, day_idx=day_idx, minute=int(m),
                        second=int(rng.integers(0, 60)), scenario_id=scenario_id,
                    )
                notes = (
                    f"{n_trades} trades in ~10 minutes for an account averaging "
                    f"{persona.daily_rate:.0f} trades/day"
                )
                window = (minutes[0], minutes[-1])

            who = ctx.account_name(account_id)
            ticker = ctx.ticker(sec_id)
            title = {
                "size_spike": f"Outsized {ticker} order by {who}",
                "price_outlier": f"Off-market {ticker} execution by {who}",
                "odd_hour": f"Out-of-pattern {ticker} timing by {who}",
                "frequency_burst": f"Rapid {ticker} order burst by {who}",
            }[variant]

            out.labels.append(
                ScenarioLabel(
                    scenario_id=scenario_id,
                    title=title,
                    case_ref="",
                    scenario_type="statistical_outlier",
                    subtype=variant,
                    label="positive",
                    expected_layer="statistical",
                    account_ids=[account_id],
                    security_ids=[sec_id],
                    window_start=ctx.timestamp(day_idx, window[0], 0),
                    window_end=ctx.timestamp(day_idx, window[1], 59),
                    difficulty=(
                        "easy" if variant in ("size_spike", "price_outlier") else "medium"
                    ),
                    notes=f"{variant}: {notes}",
                )
            )
    return out
