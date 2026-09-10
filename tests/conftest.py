from __future__ import annotations

import numpy as np
import pytest

from surveillance.config import Settings
from surveillance.generator.pipeline import GeneratorParams, generate

SEED = 20240917


@pytest.fixture(scope="session")
def dataset():
    """Full generated dataset. Generation is ~1s, so the session-scoped real thing is
    cheaper and far more meaningful than a mocked stand-in."""
    return generate(Settings(seed=SEED), GeneratorParams())


@pytest.fixture(scope="session")
def labels_by_id(dataset):
    return {label.scenario_id: label for label in dataset.labels}


@pytest.fixture(scope="session")
def account_baselines(dataset):
    """Per-account log-notional mean/sd computed from BACKGROUND trades only.

    Deriving the baseline from unplanted activity is the whole point: it is the
    counterfactual 'what does this account normally look like' that every claim about
    scenario difficulty is measured against.
    """
    df = dataset.trades
    bg = df[df["scenario_id"].isna()].copy()
    bg["notional"] = bg["quantity"] * bg["price"]
    g = bg.groupby("account_id")["notional"]
    out = g.agg(
        n="size",
        log_mu=lambda s: float(np.log(s).mean()),
        log_sd=lambda s: float(np.log(s).std()),
    )
    return out[(out["n"] >= 20) & np.isfinite(out["log_sd"]) & (out["log_sd"] > 0)]


def zscores_for(dataset, account_baselines, scenario_ids):
    """Account-relative log-notional z-scores for the trades in the given scenarios."""
    df = dataset.trades
    sub = df[df["scenario_id"].isin(scenario_ids)].copy()
    sub["notional"] = sub["quantity"] * sub["price"]
    joined = sub.join(account_baselines, on="account_id", how="inner")
    return ((np.log(joined["notional"]) - joined["log_mu"]) / joined["log_sd"]).to_numpy()
