"""Scenario-to-alert matching, implementing `docs/evaluation-contract.md`.

The contract was fixed in Phase 1, before any detector existed. That timing is the whole
point: a matching rule chosen after seeing detector output is a free parameter that can be
tuned until the numbers look good.

Why matching at all: a planted case is a *group of trades over a window*, and a detector
emits alerts on trades or on account groups. Deciding whether an alert "caught" a case is
therefore a judgement that has to be written down once and applied uniformly.

Why scenario-level rather than trade-level: an analyst investigating a 16-trade order
burst needs one alert, not sixteen. Trade-level recall also silently weights the metric by
how many trades each typology happens to contain -- with 125 burst trades against 14 size
spikes, a trade-level average is mostly a measurement of burst detection wearing a
headline number's clothes.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

#: Minimum account-set Jaccard for a group alert to be credited with a group case.
#: Sensitivity to this choice is reported rather than asserted -- see `jaccard_sweep`.
DEFAULT_JACCARD = 0.5

#: Typologies matched on shared trades rather than on account-set overlap.
POINT_TYPOLOGIES = frozenset({"statistical_outlier"})


@dataclass(slots=True)
class AlertLike:
    """The minimum an alert must expose to be scored. Deliberately not the ORM model, so
    the matcher works equally on database rows and on in-memory detector output."""

    alert_id: str
    trade_external_ids: set[str] = field(default_factory=set)
    account_ids: set[int] = field(default_factory=set)
    window_start: datetime | None = None
    window_end: datetime | None = None
    detection_method: tuple[str, ...] = ()
    alert_type: str = ""


@dataclass(slots=True)
class MatchResult:
    detected: dict[str, list[str]]          # scenario_id -> matching alert ids
    alert_to_scenario: dict[str, str]       # alert id -> scenario it is credited to
    unmatched_alerts: list[str]             # alerts matching no positive case
    hard_negative_hits: dict[str, list[str]]  # hard-negative scenario -> alert ids


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def windows_overlap(a_start, a_end, b_start, b_end) -> bool:
    """Missing windows are treated as always-overlapping: an alert that declines to state
    a window should not escape scrutiny on a technicality."""
    if a_start is None or a_end is None or b_start is None or b_end is None:
        return True
    return a_start <= b_end and b_start <= a_end


def matches(alert: AlertLike, label, *, jaccard_threshold: float = DEFAULT_JACCARD) -> bool:
    if label.scenario_type in POINT_TYPOLOGIES:
        return bool(alert.trade_external_ids & set(label.trade_external_ids))
    if not windows_overlap(
        alert.window_start, alert.window_end, label.window_start, label.window_end
    ):
        return False
    # A group alert may also be credited on shared trades: if it names the very trades that
    # were planted, quibbling about account-set overlap would be pedantry.
    if alert.trade_external_ids & set(label.trade_external_ids):
        return True
    return jaccard(alert.account_ids, set(label.account_ids)) >= jaccard_threshold


def match_all(
    alerts: Sequence[AlertLike],
    labels: Iterable,
    *,
    jaccard_threshold: float = DEFAULT_JACCARD,
) -> MatchResult:
    labels = list(labels)
    positives = [x for x in labels if x.label == "positive"]
    hard_negatives = [x for x in labels if x.label == "hard_negative"]

    detected: dict[str, list[str]] = {x.scenario_id: [] for x in positives}
    hn_hits: dict[str, list[str]] = {x.scenario_id: [] for x in hard_negatives}
    alert_to_scenario: dict[str, str] = {}
    unmatched: list[str] = []

    for alert in alerts:
        hit = None
        for label in positives:
            if matches(alert, label, jaccard_threshold=jaccard_threshold):
                hit = label.scenario_id
                detected[label.scenario_id].append(alert.alert_id)
                break
        if hit is not None:
            alert_to_scenario[alert.alert_id] = hit
            continue

        unmatched.append(alert.alert_id)
        # An unmatched alert may still be explainable: if it lands on a planted hard
        # negative, that is not merely "a false positive" -- it is a *named* failure, and
        # reporting it as such is the difference between a metric and a diagnosis.
        for label in hard_negatives:
            if matches(alert, label, jaccard_threshold=jaccard_threshold):
                hn_hits[label.scenario_id].append(alert.alert_id)
                break

    return MatchResult(
        detected=detected,
        alert_to_scenario=alert_to_scenario,
        unmatched_alerts=unmatched,
        hard_negative_hits={k: v for k, v in hn_hits.items() if v},
    )


def recall_by(result: MatchResult, labels: Iterable, attr: str = "scenario_type") -> dict:
    """Detection rate grouped by any label attribute (scenario_type, subtype, difficulty)."""
    buckets: dict[str, list[bool]] = {}
    for label in labels:
        if label.label != "positive":
            continue
        key = getattr(label, attr)
        buckets.setdefault(key, []).append(bool(result.detected.get(label.scenario_id)))
    return {k: (sum(v), len(v), sum(v) / len(v)) for k, v in sorted(buckets.items())}


def jaccard_sweep(
    alerts: Sequence[AlertLike], labels: Iterable, thresholds=(0.1, 0.25, 0.5, 0.75, 0.9)
) -> dict[float, tuple[int, int]]:
    """How much does the headline depend on the 0.5 in the contract?

    A metric that moves sharply with an arbitrary constant is not a measurement, and the
    only way to know which kind you have is to vary it and look.
    """
    labels = list(labels)
    n_positive = sum(1 for x in labels if x.label == "positive")
    out = {}
    for thr in thresholds:
        res = match_all(alerts, labels, jaccard_threshold=thr)
        out[thr] = (sum(1 for v in res.detected.values() if v), n_positive)
    return out
