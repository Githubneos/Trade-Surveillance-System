"""Fusion: combine both detection layers into one alert stream.

The job is not to produce a cleverer score. It is to produce **one alert per real concern**,
carrying enough context that an analyst can act on it, and attributing which layer found it.

Two design choices worth defending:

**Explainable weighting, not a second model.** A meta-classifier over the two layers' scores
would probably rank marginally better and would destroy the one thing the per-typology
design bought: an analyst being able to read why an alert exists. Compliance alerts are
evidence in a regulatory process. "The model said so" is not evidence.

**Deduplication by natural key.** Re-running detection must not create a second copy of the
same finding. Each alert derives a deterministic key from what it is about -- typology,
instrument, participants, window -- so re-running is idempotent at the database level.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from surveillance.db.enums import AlertType, Severity
from surveillance.detect.ring_rules import NetworkAlert
from surveillance.detect.statistical import TypologyAlert

#: Which alert_type each per-trade rule maps to.
RULE_TO_TYPE = {
    "size_spike": AlertType.STATISTICAL_OUTLIER,
    "price_outlier": AlertType.STATISTICAL_OUTLIER,
    "odd_hour": AlertType.STATISTICAL_OUTLIER,
    "frequency_burst": AlertType.STATISTICAL_OUTLIER,
}

#: Score thresholds for severity. Deliberately coarse: an analyst triages by band, and
#: pretending to more precision than the underlying score supports would be false comfort.
SEVERITY_BANDS = ((0.85, Severity.CRITICAL), (0.7, Severity.HIGH), (0.5, Severity.MEDIUM))

#: How much a corroborating signal from the other layer lifts an alert's score. Both layers
#: firing on the same accounts is genuinely stronger evidence than either alone, because
#: they are near-independent: one looks at trade magnitudes, the other at relationships.
CORROBORATION_BONUS = 0.15


def severity_for(score: float) -> Severity:
    for threshold, severity in SEVERITY_BANDS:
        if score >= threshold:
            return severity
    return Severity.LOW


@dataclass(slots=True)
class FusedAlert:
    dedup_key: str
    alert_type: AlertType
    severity: Severity
    score: float
    detection_method: tuple[str, ...]
    account_ids: tuple[int, ...]
    trade_external_ids: tuple[str, ...]
    security_id: int | None
    window_start: datetime | None
    window_end: datetime | None
    details: dict = field(default_factory=dict)
    primary_trade_external_id: str | None = None


def _key(*parts: object) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.blake2b(raw.encode(), digest_size=16).hexdigest()


def fuse(
    typology_alerts: list[TypologyAlert],
    network_alerts: list[NetworkAlert],
) -> list[FusedAlert]:
    """Collapse both layers into one deduplicated, attributed alert stream."""
    fused: list[FusedAlert] = []

    # --- graph alerts: already group-level, one per community -------------------------
    graph_accounts: dict[int, list[NetworkAlert]] = {}
    for alert in network_alerts:
        for account_id in alert.members:
            graph_accounts.setdefault(account_id, []).append(alert)

    for alert in network_alerts:
        fused.append(
            FusedAlert(
                dedup_key=_key(
                    alert.alert_type, alert.security_id, alert.members, alert.window_start
                ),
                alert_type=AlertType(alert.alert_type),
                severity=severity_for(alert.score),
                score=round(alert.score, 4),
                detection_method=("graph",),
                account_ids=tuple(alert.members),
                trade_external_ids=tuple(alert.trade_external_ids),
                security_id=alert.security_id,
                window_start=pd.Timestamp(alert.window_start).to_pydatetime(),
                window_end=pd.Timestamp(alert.window_end).to_pydatetime(),
                details=dict(alert.details),
            )
        )

    # --- per-trade alerts: one trade can breach several rules --------------------------
    # Collapsed by trade, because an analyst investigating a trade wants one item of work
    # listing every rule it broke, not one item per rule.
    by_trade: dict[str, list[TypologyAlert]] = {}
    for alert in typology_alerts:
        by_trade.setdefault(alert.external_id, []).append(alert)

    for external_id, alerts in by_trade.items():
        best = max(alerts, key=lambda a: a.score)
        rules = tuple(sorted(a.rule for a in alerts))
        score = min(best.score, 1.0)
        methods: tuple[str, ...] = ("statistical",)

        # Corroboration: is this account also implicated by the network layer, in an
        # overlapping window? Independent evidence from a different mechanism.
        corroborating = [
            g
            for g in graph_accounts.get(best.account_id, [])
            if g.window_start <= pd.Timestamp(best.executed_at) <= g.window_end
        ]
        if corroborating:
            score = min(score + CORROBORATION_BONUS, 1.0)
            methods = ("statistical", "graph")

        fused.append(
            FusedAlert(
                dedup_key=_key("statistical", external_id, rules),
                alert_type=AlertType.STATISTICAL_OUTLIER,
                severity=severity_for(score),
                score=round(score, 4),
                detection_method=methods,
                account_ids=(best.account_id,),
                trade_external_ids=(external_id,),
                security_id=best.security_id,
                window_start=pd.Timestamp(best.executed_at).to_pydatetime(),
                window_end=pd.Timestamp(best.executed_at).to_pydatetime(),
                primary_trade_external_id=external_id,
                details={
                    "rules": list(rules),
                    "feature": best.feature,
                    "value": round(best.value, 4),
                    "within_rule_percentile": round(best.score, 4),
                    "corroborated_by_network": bool(corroborating),
                    "rule": f"per-trade rule(s) breached: {', '.join(rules)}",
                },
            )
        )

    fused.sort(key=lambda a: (-a.score, a.dedup_key))
    return fused
