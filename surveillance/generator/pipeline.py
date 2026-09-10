"""Top-level synthetic data generation pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from surveillance.config import Settings, get_settings
from surveillance.generator import scenarios
from surveillance.generator.background import extend, generate_background
from surveillance.generator.market import build_market, trading_days
from surveillance.generator.personas import build_personas
from surveillance.generator.reference import build_accounts, build_securities
from surveillance.generator.rng import rng_for
from surveillance.generator.scenarios.base import ScenarioContext
from surveillance.generator.writer import Dataset, build_dataset


@dataclass(slots=True)
class GeneratorParams:
    n_accounts: int = 350
    n_securities: int = 60
    n_days: int = 20


def generate(settings: Settings | None = None, params: GeneratorParams | None = None) -> Dataset:
    settings = settings or get_settings()
    params = params or GeneratorParams()
    seed = settings.seed

    securities = build_securities(seed, params.n_securities)
    accounts = build_accounts(seed, params.n_accounts)
    days = trading_days(n_days=params.n_days)
    market = build_market(seed, securities, days)
    personas = build_personas(seed, accounts, securities)

    records = generate_background(rng_for(seed, "background"), personas, market)

    ctx = ScenarioContext(
        personas=personas,
        securities={s.id: s for s in securities},
        market=market,
        scale=min(1.0, params.n_accounts / 350),
    )

    labels = []
    # Order is deliberate: the scenarios with the tightest membership constraints claim
    # their accounts and securities first. Each stream is seeded by name, so this ordering
    # does not couple the scenarios' random draws to one another.
    for name, module in (
        ("wash_ring", scenarios.wash_ring),
        ("coordinated", scenarios.coordinated),
        ("hard_negatives", scenarios.hard_negatives),
        ("statistical", scenarios.statistical),
    ):
        out = module.generate(rng_for(seed, f"scenario.{name}"), ctx)
        extend(records, out.trades)
        labels.extend(out.labels)

    return build_dataset(seed, market, accounts, securities, records, labels)
