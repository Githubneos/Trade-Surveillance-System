"""Ground-truth labels for planted scenarios.

These records are written to ``data/ground_truth.jsonl`` and are deliberately NOT loaded
into any database table the API or the detectors can reach. The evaluation harness is the
only consumer. Keeping labels out of the serving path is what makes the reported
precision/recall trustworthy rather than self-graded.

``label`` is a three-way distinction, not a boolean:

* ``positive``      -- market abuse was planted here; a detector should fire.
* ``hard_negative`` -- legitimate activity deliberately shaped to look abusive; a detector
                       should NOT fire. These are reported separately and by name, because
                       averaging them into an overall precision number hides the failure.
* ``benign``        -- reserved for background activity; not emitted as records.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

Label = Literal["positive", "hard_negative"]


@dataclass(slots=True)
class ScenarioLabel:
    scenario_id: str
    #: Human-readable case title, composed from the scenario's own contents -- the
    #: instrument, the parties, the size of the group. Generated rather than hardcoded, so
    #: a title always describes the trades that actually got planted.
    title: str
    #: Chronological case reference, assigned after generation once every scenario's window
    #: is known (see writer.build_dataset). Reads like a case log rather than a loop index.
    case_ref: str
    scenario_type: str
    #: Optional finer-grained family within scenario_type, e.g. the 'size_spike' variant
    #: of a statistical_outlier. Reported separately because aggregating variants of very
    #: different difficulty into one row hides which ones a detector actually catches.
    subtype: str
    label: Label
    #: Which detection layer is *expected* to catch this scenario: "statistical", "graph",
    #: or "none" for hard negatives. Declared by the scenario author up front, so Phase 5
    #: can report attribution ("which layer caught what") against a stated expectation
    #: rather than rationalising whatever the detectors happen to do.
    expected_layer: str
    account_ids: list[int]
    security_ids: list[int]
    window_start: datetime
    window_end: datetime
    #: Populated after external ids are assigned (see writer.finalise_ids).
    trade_external_ids: list[str] = field(default_factory=list)
    difficulty: str = "medium"
    #: What this scenario is testing, in one line. Shows up in the evaluation report.
    notes: str = ""

    def to_json(self) -> str:
        d = asdict(self)
        d["window_start"] = self.window_start.isoformat()
        d["window_end"] = self.window_end.isoformat()
        return json.dumps(d)


def write_labels(labels: list[ScenarioLabel], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for label in sorted(labels, key=lambda x: x.scenario_id):
            fh.write(label.to_json() + "\n")


def read_labels(path: Path) -> list[ScenarioLabel]:
    out: list[ScenarioLabel] = []
    with path.open() as fh:
        for line in fh:
            if not line.strip():
                continue
            d = json.loads(line)
            d["window_start"] = datetime.fromisoformat(d["window_start"])
            d["window_end"] = datetime.fromisoformat(d["window_end"])
            out.append(ScenarioLabel(**d))
    return out
