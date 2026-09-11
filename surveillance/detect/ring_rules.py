"""Turning graph candidates into alerts.

A dense community is not evidence of abuse. Dense communities appear wherever active
accounts trade the same name, which is most of the market most of the time. What converts
a candidate into a finding is the *confirmatory economics* of the typology:

**Wash rings** recycle a position. The signature is not "these accounts trade together" but
"these accounts trade together, in both directions, in one name, and nobody ends up owning
anything". Net position is the discriminator that separates a ring from a market maker: an
MM's flow is bidirectional too, but it ends the day directional because it is absorbing
real client demand.

**Coordinated clusters** are unanimous and sudden. The signature is many otherwise
unconnected accounts taking the *same* side of a thin name inside a short window, with no
prior history in it. Public news produces the superficially similar pattern -- which is why
`hn_event_comovement` exists -- and is separated by direction (two-way, not unanimous),
liquidity, and whether the participants already held the name.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from surveillance.detect.community import Candidate

#: A ring member should end close to flat. Not exactly flat: quantities vary a little, and
#: demanding perfection would only detect a generator that produces perfection.
MAX_NET_POSITION_RATIO = 0.25

#: Share of a candidate's members that must be flat. The gate is a fraction rather than a
#: maximum because community detection routinely attaches one extra account to a real ring
#: -- a bystander who traded the same thin name at the same time. Judging the group by its
#: worst member lets a single bystander veto a genuine finding, which is exactly what
#: happened to wash_ring_01 (worst member 0.54, every real member below 0.17).
MIN_FLAT_MEMBER_SHARE = 0.6

#: Minimum share of the members' activity that sits in this one instrument.
#:
#: Deliberately low. The intuition "a ring concentrates in one name" is right about the
#: ring and wrong about the accounts running it: a controlled account still does its
#: ordinary trading elsewhere, so a hundred-trade ring is a few percent of its owner's
#: activity. Set at 0.15 this gate rejected five of six real rings. It earns its place only
#: as a floor against groups with no meaningful presence in the name at all.
MIN_SECURITY_CONCENTRATION = 0.015

#: Coordinated clusters: how tightly the group's trades must sit in time, and how one-sided.
MAX_CLUSTER_SPAN_MINUTES = 180
MIN_DIRECTIONAL_UNANIMITY = 0.8

#: How much prior activity the group may already have in the name. Above this they are
#: holders repositioning, not outsiders arriving.
MAX_PRIOR_HISTORY_RATIO = 1.0


#: Candidate-filter parameters, deliberately different per typology.
#:
#: A ring is a *repeated* relationship, so it needs several co-trades per pair before the
#: pair means anything. A coordinated cluster is the opposite: each participant trades the
#: name once, so a pair shares exactly ONE co-trade and requiring more removes every real
#: cluster. What carries the signal there is not repetition but improbability -- a single
#: synchronous co-trade in a name neither account touches, at four orders of magnitude
#: above chance.
RING_CANDIDATE_PARAMS = {
    "method": "louvain",
    "min_members": 3,
    "min_events": 4,
    "min_lift": 20.0,
    "min_reciprocity": 0.3,
}
CLUSTER_CANDIDATE_PARAMS = {
    "method": "components",
    "min_members": 5,
    "min_events": 1,
    "min_lift": 500.0,
}


@dataclass(slots=True)
class NetworkAlert:
    alert_type: str
    security_id: int
    members: tuple[int, ...]
    score: float
    window_start: object
    window_end: object
    trade_external_ids: tuple[str, ...]
    details: dict


def _member_trades(trades: pd.DataFrame, candidate: Candidate) -> pd.DataFrame:
    return trades[
        (trades["security_id"] == candidate.security_id)
        & (trades["account_id"].isin(candidate.members))
        & (trades["executed_at"] >= candidate.window_start)
        & (trades["executed_at"] <= candidate.window_end)
    ]


def evaluate_ring(trades: pd.DataFrame, candidate: Candidate) -> NetworkAlert | None:
    """Score a reciprocal community as a wash ring, or reject it."""
    window = _member_trades(trades, candidate)
    if window.empty:
        return None

    signed = np.where(window["side"] == "buy", 1.0, -1.0) * window["quantity"].astype(float)
    net = pd.Series(signed).groupby(window["account_id"].to_numpy()).sum().abs()
    gross = window.groupby("account_id")["quantity"].sum().astype(float)
    flat_ratio = (net / gross.reindex(net.index)).fillna(1.0)

    # Judge the group, not its worst member. This is the discriminator against a market
    # maker: an MM's flow is bidirectional too, but it ends the session directional because
    # it is absorbing real client demand, so few of its counterparties net flat.
    flat_share = float((flat_ratio <= MAX_NET_POSITION_RATIO).mean())
    median_net = float(flat_ratio.median())
    if flat_share < MIN_FLAT_MEMBER_SHARE or median_net > MAX_NET_POSITION_RATIO:
        return None

    # Concentration: what share of these accounts' entire activity is in this one name.
    all_member_trades = trades[trades["account_id"].isin(candidate.members)]
    concentration = len(window) / max(len(all_member_trades), 1)
    if concentration < MIN_SECURITY_CONCENTRATION:
        return None

    reciprocity = float(candidate.edges["reciprocity"].median())
    lift = float(candidate.edges["opposite_lift"].median())

    score = float(
        np.clip(
            0.40 * reciprocity
            + 0.25 * min(np.log10(max(lift, 1.0)) / 3.0, 1.0)
            + 0.25 * (1.0 - median_net / MAX_NET_POSITION_RATIO)
            + 0.10 * flat_share,
            0.0,
            1.0,
        )
    )
    return NetworkAlert(
        alert_type="wash_trade_ring",
        security_id=candidate.security_id,
        members=candidate.members,
        score=score,
        window_start=window["executed_at"].min(),
        window_end=window["executed_at"].max(),
        trade_external_ids=tuple(window["external_id"].tolist()),
        details={
            "members": list(candidate.members),
            "median_reciprocity": round(reciprocity, 3),
            "median_cotrade_lift": round(lift, 1),
            "median_member_net_ratio": round(median_net, 4),
            "flat_member_share": round(flat_share, 2),
            "security_concentration": round(concentration, 3),
            "n_trades": int(len(window)),
            "rule": "reciprocal co-trading, members net flat, concentrated in one security",
        },
    )


def evaluate_cluster(
    trades: pd.DataFrame,
    candidate: Candidate,
    liquidity: dict[int, str] | None = None,
) -> NetworkAlert | None:
    """Score a same-side community as coordinated trading, or reject it."""
    window = _member_trades(trades, candidate)
    if window.empty:
        return None

    span_minutes = (
        window["executed_at"].max() - window["executed_at"].min()
    ).total_seconds() / 60.0
    if span_minutes > MAX_CLUSTER_SPAN_MINUTES:
        return None

    # Unanimity must be measured across EVERYONE trading the name in this window, not just
    # across the candidate's members. Same-side edges only ever connect accounts trading
    # the same direction, so a component is unanimous by construction and gating on its
    # internal direction tests nothing -- measured, that vacuous gate let all three
    # news-co-movement hard negatives through.
    #
    # The real discriminator is what the rest of the market was doing. An information leak
    # produces one-way accumulation while nobody else reacts; a public announcement
    # produces two-way flow as some holders buy and others take profit.
    market = trades[
        (trades["security_id"] == candidate.security_id)
        & (trades["executed_at"] >= candidate.window_start)
        & (trades["executed_at"] <= candidate.window_end)
    ]
    market_buy_share = float((market["side"] == "buy").mean()) if len(market) else 0.5
    unanimity = max(market_buy_share, 1.0 - market_buy_share)
    if unanimity < MIN_DIRECTIONAL_UNANIMITY:
        return None

    # Did these accounts already hold the name? Coordinated leakage shows up in instruments
    # the participants have no history in.
    prior = trades[
        (trades["security_id"] == candidate.security_id)
        & (trades["account_id"].isin(candidate.members))
        & (trades["executed_at"] < candidate.window_start)
    ]
    prior_ratio = len(prior) / max(len(window), 1)
    # Leakage shows up in instruments the participants have no position in. Holders
    # repositioning after an announcement is the ordinary case, and the ordinary case must
    # not generate alerts.
    if prior_ratio > MAX_PRIOR_HISTORY_RATIO:
        return None

    tier = (liquidity or {}).get(candidate.security_id, "mid")
    tier_weight = {"illiquid": 1.0, "mid": 0.65, "liquid": 0.3}.get(tier, 0.5)
    lift = float(candidate.edges["same_lift"].median())

    score = float(
        np.clip(
            0.35 * min(np.log10(max(lift, 1.0)) / 3.0, 1.0)
            + 0.25 * unanimity
            + 0.20 * tier_weight
            + 0.20 * (1.0 / (1.0 + prior_ratio)),
            0.0,
            1.0,
        )
    )
    return NetworkAlert(
        alert_type="coordinated_cluster",
        security_id=candidate.security_id,
        members=candidate.members,
        score=score,
        window_start=window["executed_at"].min(),
        window_end=window["executed_at"].max(),
        trade_external_ids=tuple(window["external_id"].tolist()),
        details={
            "members": list(candidate.members),
            "median_cotrade_lift": round(lift, 1),
            "market_directional_unanimity": round(unanimity, 3),
            "span_minutes": round(span_minutes, 1),
            "liquidity_tier": tier,
            "prior_history_ratio": round(prior_ratio, 3),
            "n_accounts": len(candidate.members),
            "rule": "synchronous same-side trading in a thin name with no prior history",
        },
    )


def run_network_detection(
    trades: pd.DataFrame,
    ring_pairs: pd.DataFrame,
    cluster_pairs: pd.DataFrame | None = None,
    liquidity: dict[int, str] | None = None,
    **kwargs,
) -> list[NetworkAlert]:
    """Both graph typologies, end to end."""
    from surveillance.detect.community import find_candidates

    df = trades.copy()
    df["executed_at"] = pd.to_datetime(df["executed_at"], utc=True)
    pairs = ring_pairs
    cluster_frame = cluster_pairs if cluster_pairs is not None else ring_pairs

    ring_params = {**RING_CANDIDATE_PARAMS, **kwargs.get("ring", {})}
    cluster_params = {**CLUSTER_CANDIDATE_PARAMS, **kwargs.get("cluster", {})}

    alerts: list[NetworkAlert] = []
    for candidate in find_candidates(pairs, kind="reciprocal", **ring_params):
        alert = evaluate_ring(df, candidate)
        if alert:
            alerts.append(alert)
    for candidate in find_candidates(cluster_frame, kind="same_side", **cluster_params):
        alert = evaluate_cluster(df, candidate, liquidity)
        if alert:
            alerts.append(alert)
    return alerts
