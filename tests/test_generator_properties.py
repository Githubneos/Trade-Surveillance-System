"""Properties the synthetic dataset must hold for the evaluation to mean anything.

These are not smoke tests. Each one guards a claim that the project's headline metrics
depend on, and each would fail loudly if a future change to the generator quietly made the
detection problem easier.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.conftest import zscores_for

#: Fraction of ordinary background trades that land beyond |z|=3 purely by chance. Every
#: claim about a scenario being "hidden" or "obvious" is expressed relative to this.
BACKGROUND_TAIL_TOLERANCE = 0.03


def _scenario_ids(labels_by_id, scenario_type):
    return [k for k, v in labels_by_id.items() if v.scenario_type == scenario_type]


def test_background_tail_rate(dataset, account_baselines):
    """Sanity floor: ordinary activity should have a thin, lognormal-looking tail."""
    df = dataset.trades
    bg = df[df["scenario_id"].isna()].copy()
    bg["notional"] = bg["quantity"] * bg["price"]
    joined = bg.join(account_baselines, on="account_id", how="inner")
    z = (np.log(joined["notional"]) - joined["log_mu"]) / joined["log_sd"]
    assert float(np.mean(np.abs(z) > 3)) < 0.01


def test_wash_ring_trades_are_individually_unremarkable(
    dataset, labels_by_id, account_baselines
):
    """THE load-bearing property of this project.

    If wash-ring trades were individually large or oddly priced, the per-trade statistical
    scorer would catch them and the entire graph layer would be unmotivated. The rings must
    be invisible one trade at a time and only visible as structure.
    """
    z = zscores_for(dataset, account_baselines, _scenario_ids(labels_by_id, "wash_ring"))
    assert len(z) > 100, "expected a substantial number of ring trades to evaluate"
    outlier_rate = float(np.mean(np.abs(z) > 3))
    assert outlier_rate <= BACKGROUND_TAIL_TOLERANCE, (
        f"{outlier_rate:.1%} of wash-ring trades are size outliers; the per-trade scorer "
        "would catch them and the graph layer would prove nothing"
    )
    assert abs(float(np.median(z))) < 0.5


def test_wash_ring_members_end_flat(dataset, labels_by_id):
    """A wash ring recycles the same position; nobody accumulates one."""
    df = dataset.trades
    for scenario_id in _scenario_ids(labels_by_id, "wash_ring"):
        g = df[df["scenario_id"] == scenario_id]
        signed = np.where(g["side"] == "buy", 1.0, -1.0) * g["quantity"].to_numpy()
        gross = g["quantity"].to_numpy()
        net_by_account: dict[int, float] = {}
        gross_by_account: dict[int, float] = {}
        for aid, s, gr in zip(g["account_id"], signed, gross, strict=True):
            net_by_account[aid] = net_by_account.get(aid, 0.0) + s
            gross_by_account[aid] = gross_by_account.get(aid, 0.0) + gr
        for aid, net in net_by_account.items():
            ratio = abs(net) / gross_by_account[aid]
            assert ratio < 0.02, f"{scenario_id} account {aid} ended {ratio:.1%} directional"


def test_wash_rings_are_closed_cycles(dataset, labels_by_id):
    """Every ring trade must have a counterparty inside the ring -- that is what makes it
    a cycle rather than ordinary trading that happens to net out."""
    df = dataset.trades
    for scenario_id in _scenario_ids(labels_by_id, "wash_ring"):
        g = df[df["scenario_id"] == scenario_id]
        members = set(labels_by_id[scenario_id].account_ids)
        cps = g["counterparty_account_id"].dropna().astype(int)
        assert len(cps) == len(g), "every ring leg should record a counterparty"
        assert set(cps).issubset(members)


def test_size_spikes_are_genuinely_extreme(dataset, labels_by_id, account_baselines):
    """The 'easy' positives must actually be easy, or measured recall on the statistical
    layer says nothing about the layer."""
    ids = [k for k, v in labels_by_id.items() if k.startswith("stat_size_spike")]
    z = zscores_for(dataset, account_baselines, ids)
    assert len(z) >= 10
    assert float(np.mean(z > 3)) > 0.9
    assert float(np.median(z)) > 4.0


@pytest.mark.parametrize(
    "scenario_type",
    ["mm_two_sided", "legit_block", "event_comovement", "liquid_crowding"],
)
def test_hard_negatives_are_not_size_anomalies(
    dataset, labels_by_id, account_baselines, scenario_type
):
    """Hard negatives exist to test a *specific* detector failure mode. If they also
    contain size outliers they would trip the statistical layer for an unrelated reason,
    and the false positive would be an artefact of the generator rather than a finding."""
    z = zscores_for(dataset, account_baselines, _scenario_ids(labels_by_id, scenario_type))
    assert len(z) > 0
    rate = float(np.mean(np.abs(z) > 3))
    assert rate <= BACKGROUND_TAIL_TOLERANCE, (
        f"{scenario_type} contains {rate:.1%} size outliers -- it would fire the "
        "statistical layer for the wrong reason"
    )


def test_coordinated_clusters_are_subtle_individually(
    dataset, labels_by_id, account_baselines
):
    """Coordinated clustering is a timing signal, not a size signal."""
    z = zscores_for(dataset, account_baselines, _scenario_ids(labels_by_id, "coordinated_cluster"))
    assert float(np.mean(np.abs(z) > 3)) <= BACKGROUND_TAIL_TOLERANCE


def test_external_ids_do_not_leak_labels(dataset):
    """Ids are assigned after a global sort by execution time. If they were assigned per
    component, planted trades would occupy contiguous id ranges and a detector could
    'learn' the label from the primary key."""
    df = dataset.trades
    assert df["external_id"].is_unique
    assert df["executed_at"].is_monotonic_increasing

    planted = df.index[df["scenario_id"].notna()].to_numpy()
    n = len(df)
    # Under a null of random placement the planted rows' mean position is n/2. A contiguous
    # block at either end would be far outside this band.
    assert 0.35 * n < planted.mean() < 0.65 * n
    # And they must not be one unbroken run.
    assert (np.diff(planted) > 1).sum() > len(planted) * 0.5


def test_every_label_resolves_to_real_trades(dataset, labels_by_id):
    valid = set(dataset.trades["external_id"])
    for label in labels_by_id.values():
        assert label.trade_external_ids, f"{label.scenario_id} has no trades"
        assert set(label.trade_external_ids).issubset(valid)
        assert label.window_start <= label.window_end
        assert label.notes, f"{label.scenario_id} must explain what it tests"


def test_positive_scenarios_do_not_share_accounts(dataset, labels_by_id):
    """Overlapping membership between two positives would make an alert impossible to
    attribute, letting one detection be credited to the wrong scenario."""
    seen: dict[int, str] = {}
    for label in labels_by_id.values():
        if label.label != "positive":
            continue
        for aid in label.account_ids:
            assert aid not in seen, (
                f"account {aid} is in both {seen[aid]} and {label.scenario_id}"
            )
            seen[aid] = label.scenario_id


def test_generation_is_deterministic():
    from surveillance.config import Settings
    from surveillance.generator.pipeline import GeneratorParams, generate

    a = generate(Settings(seed=4242), GeneratorParams(n_accounts=60, n_securities=20, n_days=5))
    b = generate(Settings(seed=4242), GeneratorParams(n_accounts=60, n_securities=20, n_days=5))
    assert a.trades.equals(b.trades)
    assert [x.scenario_id for x in a.labels] == [x.scenario_id for x in b.labels]
