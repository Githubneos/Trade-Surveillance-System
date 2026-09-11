"""Correlating trading activity against material-news filings.

## The honest framing, stated first

Our securities are invented, so they cannot genuinely correlate with real corporate
filings. Each synthetic security is mapped to a real SEC CIK by a **documented deterministic
assignment**, and that company's real 8-K *timestamps* become the news events for it.

So the timing signal is real news timing measured against synthetic trades. It demonstrates
the mechanism and the integration end to end; it says nothing about whether trading in these
instruments anticipated anything, because there is nothing to anticipate. Any figure this
module produces is an engineering demonstration, not a finding. This is repeated in
`docs/limitations.md` and in the module's own output so it cannot be quoted out of context.

## What it measures

Two things, combined:

1. **Timing** — accumulation in the window *before* a filing, relative to that security's
   own normal activity. Trading after news is ordinary; trading before it is the concern.
2. **Materiality** — not every 8-K moves a price. A TF-IDF similarity against a vocabulary
   of genuinely price-sensitive event language weights an earnings release or a merger far
   above a routine bylaw amendment.

TF-IDF rather than a sentence-transformer: the corpus is a few hundred short, highly
formulaic item descriptions drawn from a closed vocabulary of twenty-one codes. Lexical
overlap captures essentially all the available signal, and a transformer would add a
multi-gigabyte dependency to CI and the Docker image for a stretch feature. The trade-off is
noted rather than hidden -- semantic matching would matter on free-text headlines.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

#: Real CIKs of large US filers, used only as a source of realistic 8-K timestamps.
#: The pairing with our invented tickers is arbitrary and documented as such.
REFERENCE_CIKS = [
    320193, 789019, 1018724, 1652044, 1326801, 50863, 200406, 19617, 104169, 731766,
    93410, 34088, 66740, 21344, 80424, 40545, 1467858, 4962, 63908, 320187,
    1090727, 55785, 97745, 310158, 78003, 1551152, 318154, 882095, 72971, 36104,
]

#: Language that characterises a genuinely price-sensitive disclosure. The similarity of a
#: filing's item text to this vocabulary is its materiality weight.
MATERIAL_LANGUAGE = (
    "results of operations and financial condition earnings revenue guidance "
    "completion of acquisition or disposition of assets merger tender offer "
    "bankruptcy receivership material impairment non-reliance on previously issued "
    "financial statements restatement changes in control of registrant delisting "
    "termination of a material definitive agreement"
)

#: How far before a filing counts as "ahead of the news".
LOOKBACK = timedelta(hours=48)


@dataclass(slots=True)
class NewsSignal:
    security_id: int
    ticker: str
    cik: int
    company: str
    filed_at: object
    headline: str
    materiality: float
    pre_news_trades: int
    baseline_trades: float
    activity_lift: float
    score: float
    #: Whether this breached both thresholds. Non-breaching evaluations are returned too,
    #: because "we looked at ten filings and none showed abnormal accumulation" is a
    #: result, while an empty list is indistinguishable from a broken pipeline.
    flagged: bool = False


def assign_ciks(security_ids: list[int], ciks: list[int] | None = None) -> dict[int, int]:
    """Deterministically map each synthetic security to a real CIK.

    Hash-based rather than positional so the mapping is stable when securities are added,
    and so it is obviously arbitrary -- which it is, and which matters more than it being
    convenient.
    """
    pool = ciks or REFERENCE_CIKS
    out = {}
    for sid in sorted(security_ids):
        digest = hashlib.blake2b(str(sid).encode(), digest_size=4).digest()
        out[sid] = pool[int.from_bytes(digest, "big") % len(pool)]
    return out


def materiality_scores(headlines: list[str]) -> np.ndarray:
    """TF-IDF cosine similarity of each headline against price-sensitive language."""
    if not headlines:
        return np.array([])
    corpus = [*headlines, MATERIAL_LANGUAGE]
    vectoriser = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1)
    matrix = vectoriser.fit_transform(corpus)
    sims = cosine_similarity(matrix[:-1], matrix[-1]).ravel()
    return np.clip(sims, 0.0, 1.0)


def correlate(
    trades: pd.DataFrame,
    filings_by_security: dict[int, list],
    tickers: dict[int, str],
    *,
    lookback: timedelta = LOOKBACK,
    min_lift: float = 2.0,
    min_materiality: float = 0.15,
) -> list[NewsSignal]:
    """Evaluate every filing that falls inside the trading window.

    Returns an evaluation per in-window filing, flagged or not. Returning only breaches
    would make a clean run indistinguishable from a broken fetch, and on synthetic data the
    expected -- and correct -- outcome is that nothing breaches.
    """
    df = trades.copy()
    df["executed_at"] = pd.to_datetime(df["executed_at"], utc=True)

    all_filings = [f for fs in filings_by_security.values() for f in fs]
    if not all_filings:
        return []
    weights = dict(
        zip(
            [id(f) for f in all_filings],
            materiality_scores([f.headline for f in all_filings]),
            strict=True,
        )
    )

    out: list[NewsSignal] = []
    for security_id, filings in filings_by_security.items():
        sub = df[df["security_id"] == security_id]
        if sub.empty:
            continue
        span_hours = max(
            (sub["executed_at"].max() - sub["executed_at"].min()).total_seconds() / 3600, 1.0
        )
        # Expected trades in a window of this length, from the security's own rate.
        baseline = len(sub) * (lookback.total_seconds() / 3600) / span_hours

        for filing in filings:
            # EDGAR yields naive dates; callers and tests may supply aware ones. Assuming
            # either one raises on the other, so normalise explicitly.
            filed = pd.Timestamp(filing.filed_at)
            filed = (
                filed.tz_localize("UTC") if filed.tzinfo is None else filed.tz_convert("UTC")
            )
            window = sub[(sub["executed_at"] >= filed - lookback) & (sub["executed_at"] < filed)]
            if window.empty or baseline <= 0:
                continue
            lift = len(window) / baseline
            materiality = float(weights.get(id(filing), 0.0))
            flagged = lift >= min_lift and materiality >= min_materiality
            out.append(
                NewsSignal(
                    security_id=security_id,
                    ticker=tickers.get(security_id, str(security_id)),
                    cik=filing.cik,
                    company=filing.company,
                    filed_at=filed,
                    headline=filing.headline,
                    materiality=round(materiality, 3),
                    pre_news_trades=int(len(window)),
                    baseline_trades=round(float(baseline), 1),
                    activity_lift=round(float(lift), 2),
                    score=round(
                        float(min(1.0, 0.6 * min(lift / 5.0, 1.0) + 0.4 * materiality)), 4
                    ),
                    flagged=flagged,
                )
            )
    out.sort(key=lambda s: -s.score)
    return out
