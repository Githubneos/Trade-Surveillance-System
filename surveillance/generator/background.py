"""Background (non-anomalous) trading activity.

This is the part that has to look boring. If the background is too clean, every planted
anomaly stands out trivially and the reported precision/recall is meaningless.
"""

from __future__ import annotations

import numpy as np

from surveillance.generator.market import MINUTES_PER_SESSION, MarketData
from surveillance.generator.personas import DOW_MULTIPLIER, Persona

#: Column order used by every generator component.
TRADE_FIELDS = (
    "account_id",
    "security_id",
    "side",
    "quantity",
    "price",
    "day_idx",
    "minute",
    "second",
    "counterparty_account_id",
    "scenario_id",
)


def _empty_records() -> dict[str, list]:
    return {f: [] for f in TRADE_FIELDS}


def generate_background(
    rng: np.random.Generator, personas: dict[int, Persona], market: MarketData
) -> dict[str, list]:
    n_days = len(market.days)
    dow = np.array([DOW_MULTIPLIER[d.weekday()] for d in market.days])
    rec = _empty_records()

    for persona in personas.values():
        # Daily counts: Poisson around the persona rate, with a lognormal day-to-day
        # multiplier so activity is overdispersed rather than cleanly Poisson.
        day_noise = np.exp(rng.normal(0.0, 0.35, n_days))
        lam = persona.daily_rate * dow * day_noise
        counts = rng.poisson(lam)
        total = int(counts.sum())
        if total == 0:
            continue

        day_idx = np.repeat(np.arange(n_days), counts)
        minutes = rng.choice(MINUTES_PER_SESSION, size=total, p=persona.minute_pmf)
        seconds = rng.integers(0, 60, size=total)

        pick = rng.choice(len(persona.watchlist), size=total, p=persona.watch_weights)
        sec_ids = persona.watchlist[pick]

        if persona.two_sided:
            # Market makers quote both sides continuously: near-alternating flow with only
            # a slight directional lean. This is the shape that superficially resembles a
            # wash ring, and is deliberately planted as a hard negative later.
            sides_buy = np.arange(total) % 2 == 0
            flip = rng.random(total) < 0.22
            sides_buy = np.where(flip, ~sides_buy, sides_buy)
        else:
            sides_buy = rng.random(total) < persona.buy_prob

        notionals = persona.draw_notional(rng, total)

        for j in range(total):
            sid = int(sec_ids[j])
            d, mi = int(day_idx[j]), int(minutes[j])
            is_buy = bool(sides_buy[j])
            price = market.execution_price(rng, sid, d, mi, is_buy)
            qty = max(1, int(round(notionals[j] / price)))
            rec["account_id"].append(persona.account_id)
            rec["security_id"].append(sid)
            rec["side"].append("buy" if is_buy else "sell")
            rec["quantity"].append(float(qty))
            rec["price"].append(price)
            rec["day_idx"].append(d)
            rec["minute"].append(mi)
            rec["second"].append(int(seconds[j]))
            rec["counterparty_account_id"].append(None)
            rec["scenario_id"].append(None)

    return rec


def extend(target: dict[str, list], other: dict[str, list]) -> None:
    for field in TRADE_FIELDS:
        target[field].extend(other[field])
