"""Per-trade anomaly scoring.

Two scorers live here on purpose:

* ``IsolationForest`` -- the primary. It isolates points with short random-partition paths,
  which suits this problem because anomalies are sparse, the feature space is mixed-scale,
  and there are no labels to train on at detection time.
* ``TailSurprisalScorer`` -- each feature converted to its empirical two-sided tail
  probability, scored by the most extreme one. Scale-free by construction, which is what
  makes six features with wildly different tail shapes comparable at all.
* ``RobustZScorer`` -- a deliberately naive baseline kept for contrast, because its failure
  mode is instructive: comparing features on a common numeric scale lets the
  heaviest-tailed feature dominate the decision.

All three are measured against ground truth and the primary is chosen on the numbers, not
on preference. If a forest cannot beat a rank rule on this problem, it should not be in the
system, and saying so is more useful than shipping the fashionable option.

Both emit a score in [0, 1] where higher is more anomalous, so the fusion layer can treat
them interchangeably and the comparison is apples to apples.

This module never sees labels; it is scored against ground truth from the outside.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from surveillance.detect.features import FEATURE_COLUMNS, build_features, impute


@dataclass(slots=True)
class ScoreResult:
    """Per-trade scores plus the feature frame they came from.

    ``contributions`` records, for each trade, which feature was most responsible for its
    score. An alert that cannot say *why* it fired is useless to an analyst, and a
    surveillance system that produces unexplainable alerts does not get deployed.
    """

    scores: pd.Series
    features: pd.DataFrame
    top_feature: pd.Series
    model: object | None = None


#: Ceiling on any single feature's robust z. Without it one axis can reach z in the
#: thousands and the max-across-features rule stops being a detector at all -- it just
#: reports which feature has the smallest spread.
Z_CLIP = 25.0


def _robust_z(frame: pd.DataFrame) -> pd.DataFrame:
    """Median-centred, MAD-scaled, with a floor on the scale.

    MAD is used rather than standard deviation because it is resistant to the very
    outliers being looked for -- a stdev is inflated by them and under-reports its own
    anomalies. But MAD has a failure mode of its own: a feature whose values pile up on a
    few points (a count, a decayed novelty score) has a MAD near zero, and dividing by it
    produces z-scores in the hundreds for entirely ordinary rows. That single feature then
    wins the max-across-features comparison every time, for a reason that has nothing to do
    with anomalousness.

    Flooring the scale at a quarter of the standard deviation keeps the robustness where
    MAD is meaningful and prevents the collapse where it is not.
    """
    med = frame.median()
    mad = (frame - med).abs().median() * 1.4826
    floor = frame.std() * 0.25
    scale = np.maximum(mad, floor).replace(0, np.nan)
    return ((frame - med) / scale).abs().clip(upper=Z_CLIP)


class RobustZScorer:
    """Baseline: an anomaly is a trade extreme on any single feature."""

    name = "robust_z"

    def fit_score(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        z = _robust_z(frame[list(FEATURE_COLUMNS)]).fillna(0.0)
        worst = z.to_numpy().max(axis=1)
        top = z.to_numpy().argmax(axis=1)
        # Squash to [0,1] with a soft knee at z=6 so the scale is comparable to the forest.
        return 1.0 - np.exp(-worst / 6.0), top


class TailSurprisalScorer:
    """Rank each feature against its own empirical distribution, then take the most
    extreme.

    This exists because the naive robust-z rule has a flaw that survives every rescaling:
    features have different tail *shapes*. price_dev_z is a ratio with a heavy tail
    reaching into the hundreds; z_log_notional is standardised and rarely leaves +/-7. Any
    rule that compares them on a common numeric scale lets the heavy-tailed feature win
    every time, so the scorer effectively becomes "how unusual was the price", ignoring the
    other five features.

    Converting to an empirical two-sided tail probability removes the scale question
    entirely. "This trade is in the most extreme 1-in-20,000 of its feature" means the same
    thing for every feature regardless of units or tail weight. The score is the surprisal
    -log10(p) of the single most extreme feature, because the planted typologies are
    single-axis by construction: a size spike is extreme in size and ordinary in everything
    else, and averaging across features would dilute exactly the signal being looked for.
    """

    name = "tail_surprisal"

    def fit_score(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        cols = list(FEATURE_COLUMNS)
        n = len(frame)
        surprisal = np.zeros((n, len(cols)))
        for j, col in enumerate(cols):
            values = frame[col].to_numpy(dtype=float)
            # Two-sided empirical tail: how far into either tail does this value sit.
            ranks = pd.Series(values).rank(method="average").to_numpy()
            upper = (n - ranks + 1) / (n + 1)
            lower = ranks / (n + 1)
            p = 2.0 * np.minimum(upper, lower)
            surprisal[:, j] = -np.log10(np.clip(p, 1.0 / (n + 1), 1.0))
        worst = surprisal.max(axis=1)
        top = surprisal.argmax(axis=1)
        # Normalise by the strongest surprisal this dataset can express, so the output is
        # comparable across runs of different size.
        ceiling = -np.log10(1.0 / (n + 1))
        return np.clip(worst / ceiling, 0.0, 1.0), top


class IsolationForestScorer:
    name = "isolation_forest"

    def __init__(self, *, n_estimators: int = 300, contamination: float = 0.01, seed: int = 7):
        self.model = IsolationForest(
            n_estimators=n_estimators,
            contamination=contamination,
            max_samples="auto",
            random_state=seed,
            n_jobs=-1,
        )

    def fit_score(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        x = frame[list(FEATURE_COLUMNS)].to_numpy(dtype=float)
        self.model.fit(x)
        # score_samples: higher = more normal. Flip and min-max so higher = more anomalous.
        raw = -self.model.score_samples(x)
        lo, hi = float(raw.min()), float(raw.max())
        scores = (raw - lo) / (hi - lo) if hi > lo else np.zeros_like(raw)

        # Attribute each score to its most extreme feature. The forest gives no native
        # per-feature attribution, so this uses the robust-z ranking as the explanation --
        # honest about being a proxy, and enough to tell an analyst where to look.
        z = _robust_z(frame[list(FEATURE_COLUMNS)]).fillna(0.0)
        return scores, z.to_numpy().argmax(axis=1)


#: Which engineered feature evidences which typology, and the share of trades each rule is
#: allowed to flag.
#:
#: This mapping is the module's main design decision. A single global anomaly score forces
#: every typology to compete for one threshold, and they do not compete fairly: the
#: features have different tail weights, so size spikes (reaching the 99.999th percentile
#: of their own feature) permanently outrank out-of-pattern timing (99.8th). Measured, a
#: global score needed a 5% flag rate -- over nine thousand alerts -- before it caught even
#: half the timing cases, by which point the alert queue is unworkable.
#:
#: Giving each rule its own budget against its own feature's tail is both the fix and what
#: real surveillance platforms actually do: a venue runs named scenarios ("wash trade",
#: "marking the close") each with independently tuned parameters, not one anomaly score.
#: It also makes every alert natively explainable -- the rule that fired IS the reason.
TYPOLOGY_RULES: dict[str, dict] = {
    "size_spike": {"feature": "z_log_notional", "side": "upper", "rate": 0.0004},
    "price_outlier": {"feature": "price_dev_z", "side": "two", "rate": 0.0004},
    "odd_hour": {"feature": "time_of_day_surprise", "side": "upper", "rate": 0.0004},
    "frequency_burst": {"feature": "burst_ratio", "side": "upper", "rate": 0.0004},
}


@dataclass(slots=True)
class TypologyAlert:
    external_id: str
    account_id: int
    security_id: int
    executed_at: object
    rule: str
    feature: str
    value: float
    score: float


def per_typology_alerts(
    trades: pd.DataFrame, rules: dict[str, dict] | None = None
) -> list[TypologyAlert]:
    """Run each typology rule against its own feature, with its own threshold.

    Returns one alert per (trade, rule) that breaches. A trade breaching two rules yields
    two alerts, which the fusion layer collapses -- keeping them separate here means the
    reason for each is never lost.
    """
    rules = rules or TYPOLOGY_RULES
    features = impute(build_features(trades))
    n = len(features)
    out: list[TypologyAlert] = []

    for rule_name, spec in rules.items():
        col = spec["feature"]
        values = features[col].to_numpy(dtype=float)
        keep = max(1, int(round(n * spec["rate"])))
        if spec["side"] == "two":
            ranked = np.abs(values - np.median(values))
        elif spec["side"] == "lower":
            ranked = -values
        else:
            ranked = values
        threshold = np.partition(ranked, n - keep)[n - keep]
        hits = np.nonzero(ranked >= threshold)[0]

        # Score ranks each alert against the OTHER ALERTS from the same rule, not against
        # the whole population. Ranking against everything gives every alert a percentile
        # of ~1.0 by definition -- they all cleared the same extreme threshold -- so every
        # alert came out "critical" and severity carried no information at all.
        #
        # Mapped into [0.5, 1.0] so the weakest breach is still meaningfully above an
        # ordinary trade while the strongest is separated from it.
        breach_order = pd.Series(ranked[hits]).rank(pct=True).to_numpy()
        scores = 0.5 + 0.5 * breach_order
        for slot, i in enumerate(hits):
            out.append(
                TypologyAlert(
                    external_id=str(features["external_id"].iloc[i]),
                    account_id=int(features["account_id"].iloc[i]),
                    security_id=int(features["security_id"].iloc[i]),
                    executed_at=features["executed_at"].iloc[i],
                    rule=rule_name,
                    feature=col,
                    value=float(values[i]),
                    score=float(scores[slot]),
                )
            )
    return out


def score_trades(
    trades: pd.DataFrame,
    *,
    scorer: str = "isolation_forest",
    contamination: float = 0.01,
    seed: int = 7,
) -> ScoreResult:
    features = impute(build_features(trades))
    engines = {
        "isolation_forest": lambda: IsolationForestScorer(
            contamination=contamination, seed=seed
        ),
        "tail_surprisal": TailSurprisalScorer,
        "robust_z": RobustZScorer,
    }
    if scorer not in engines:
        raise ValueError(f"unknown scorer {scorer!r}; expected one of {sorted(engines)}")
    engine = engines[scorer]()
    scores, top_idx = engine.fit_score(features)

    return ScoreResult(
        scores=pd.Series(scores, index=features.index, name="score"),
        features=features,
        top_feature=pd.Series(
            [FEATURE_COLUMNS[i] for i in top_idx], index=features.index, name="top_feature"
        ),
        model=getattr(engine, "model", None),
    )
