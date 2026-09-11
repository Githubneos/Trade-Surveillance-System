"""SEC EDGAR client for 8-K (material event) filings.

Two things this module is careful about, because getting either wrong gets you blocked:

* **Identification.** SEC requires a descriptive User-Agent with contact details on every
  request. Anonymous scraping is explicitly against their access policy.
* **Rate.** SEC's documented limit is 10 requests/second. We stay well under it, and cache
  every response to disk so repeat runs, tests and CI make no network calls at all.

If the network is unavailable the client degrades to cache-only and says so, rather than
failing the pipeline: a stretch signal must never be able to take down detection.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

#: SEC asks for a real contact. Override with SURV_SEC_USER_AGENT in any real deployment.
DEFAULT_USER_AGENT = "TradeSurveillancePortfolio/0.1 (contact: research@example.com)"

#: Comfortably inside SEC's documented 10 req/s ceiling.
MIN_REQUEST_INTERVAL_S = 0.15


@dataclass(slots=True)
class Filing:
    cik: int
    company: str
    form: str
    filed_at: datetime
    accession: str
    headline: str


@dataclass(slots=True)
class EdgarClient:
    cache_dir: Path
    user_agent: str = DEFAULT_USER_AGENT
    offline: bool = False
    _last_request: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, cik: int) -> Path:
        return self.cache_dir / f"cik-{cik:010d}.json"

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < MIN_REQUEST_INTERVAL_S:
            time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)
        self._last_request = time.monotonic()

    def fetch_submissions(self, cik: int) -> dict | None:
        """Company submission history, from cache when available."""
        path = self._cache_path(cik)
        if path.exists():
            return json.loads(path.read_text())
        if self.offline:
            return None

        self._throttle()
        request = urllib.request.Request(
            SUBMISSIONS_URL.format(cik=cik),
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    import gzip

                    raw = gzip.decompress(raw)
                payload = json.loads(raw)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            # Never fail the pipeline for a stretch signal.
            log.warning("EDGAR fetch failed for CIK %s: %s", cik, exc)
            return None

        path.write_text(json.dumps(payload))
        return payload

    def recent_8k(self, cik: int, limit: int = 60) -> list[Filing]:
        payload = self.fetch_submissions(cik)
        if not payload:
            return []
        company = payload.get("name", f"CIK {cik}")
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        items = recent.get("items", [""] * len(forms))
        primary = recent.get("primaryDocDescription", [""] * len(forms))

        out: list[Filing] = []
        for i, form in enumerate(forms):
            if form != "8-K":
                continue
            try:
                filed = datetime.fromisoformat(dates[i])
            except (ValueError, IndexError):
                continue
            # EDGAR's item codes are the actual signal of what the filing is about;
            # primaryDocDescription is often empty or boilerplate.
            headline = describe_items(items[i] if i < len(items) else "")
            if i < len(primary) and primary[i]:
                headline = f"{headline} — {primary[i]}"
            out.append(
                Filing(
                    cik=cik, company=company, form=form, filed_at=filed,
                    accession=accessions[i] if i < len(accessions) else "",
                    headline=headline,
                )
            )
            if len(out) >= limit:
                break
        return out


#: 8-K item codes. A filing's items are the closest thing EDGAR gives to a headline, and
#: expanding them into prose is what makes TF-IDF similarity meaningful -- "2.02" carries
#: no lexical signal, "results of operations and financial condition" does.
ITEM_DESCRIPTIONS = {
    "1.01": "entry into a material definitive agreement",
    "1.02": "termination of a material definitive agreement",
    "1.03": "bankruptcy or receivership",
    "2.01": "completion of acquisition or disposition of assets",
    "2.02": "results of operations and financial condition",
    "2.03": "creation of a direct financial obligation",
    "2.04": "triggering events that accelerate a financial obligation",
    "2.05": "costs associated with exit or disposal activities",
    "2.06": "material impairments",
    "3.01": "notice of delisting or failure to satisfy a listing rule",
    "3.02": "unregistered sales of equity securities",
    "3.03": "material modification to rights of security holders",
    "4.01": "changes in registrant's certifying accountant",
    "4.02": "non-reliance on previously issued financial statements",
    "5.01": "changes in control of registrant",
    "5.02": "departure or election of directors or principal officers",
    "5.03": "amendments to articles of incorporation or bylaws",
    "5.07": "submission of matters to a vote of security holders",
    "7.01": "regulation FD disclosure",
    "8.01": "other events",
    "9.01": "financial statements and exhibits",
}


def describe_items(items: str) -> str:
    """Turn '2.02,9.01' into readable prose for the embedding step."""
    codes = [c.strip() for c in (items or "").split(",") if c.strip()]
    described = [ITEM_DESCRIPTIONS.get(c, f"item {c}") for c in codes]
    return "; ".join(described) if described else "material event disclosure"
