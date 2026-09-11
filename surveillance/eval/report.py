"""The honest scorecard.

Reports what the detectors actually do, including where they fail. Three commitments this
module exists to keep:

1. **Per-typology, never one aggregate.** Typologies differ by an order of magnitude in
   difficulty, and a single headline recall is mostly a measurement of whichever typology
   contributed the most cases.
2. **Hard negatives named, not averaged.** A planted look-alike that fires is a diagnosis,
   not a rounding error in a precision figure. It gets listed by name with what it was
   testing.
3. **Layer attribution against a stated expectation.** Each case declared which layer
   should catch it *when it was planted*, so the report compares against that rather than
   rationalising whatever the detectors happened to do.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from surveillance.detect.fusion import FusedAlert
from surveillance.eval.matching import AlertLike, jaccard_sweep, match_all, recall_by


def to_alertlike(alerts: list[FusedAlert]) -> list[AlertLike]:
    return [
        AlertLike(
            alert_id=a.dedup_key,
            trade_external_ids=set(a.trade_external_ids),
            account_ids=set(a.account_ids),
            window_start=a.window_start,
            window_end=a.window_end,
            detection_method=a.detection_method,
            alert_type=a.alert_type.value,
        )
        for a in alerts
    ]


def print_report(alerts: list[FusedAlert], labels, trades, console: Console | None = None):
    console = console or Console(width=150)
    scored = to_alertlike(alerts)
    result = match_all(scored, labels)
    by_id = {x.scenario_id: x for x in labels}

    n_positive = sum(1 for x in labels if x.label == "positive")
    detected = sum(1 for v in result.detected.values() if v)
    matched_alerts = len(result.alert_to_scenario)

    # --- headline ----------------------------------------------------------------------
    t = Table(title="Detection scorecard", title_justify="left", header_style="bold")
    t.add_column("metric")
    t.add_column("value", justify="right")
    t.add_row("trades scored", f"{len(trades):,}")
    t.add_row("alerts raised", f"{len(alerts):,}")
    t.add_row("alert rate", f"{len(alerts) / max(len(trades), 1):.3%} of trades")
    t.add_row("cases detected", f"{detected}/{n_positive}  ({detected / n_positive:.0%})")
    t.add_row(
        "alert precision",
        f"{matched_alerts}/{len(alerts)}  ({matched_alerts / max(len(alerts), 1):.0%})",
    )
    t.add_row(
        "hard negatives fired",
        f"{len(result.hard_negative_hits)}/"
        f"{sum(1 for x in labels if x.label == 'hard_negative')}",
    )
    console.print(t)

    # --- per typology, split by which layer found it -------------------------------------
    t = Table(
        title="\nBy typology  --  'found by' is measured, 'expected' was declared when the "
        "case was planted",
        title_justify="left",
        header_style="bold",
    )
    for col in ("typology", "cases", "detected", "recall", "expected layer", "found by"):
        t.add_column(col, justify="right" if col in ("cases", "detected", "recall") else "left")

    by_subtype = recall_by(result, labels, "subtype")
    for subtype, (got, total, rate) in by_subtype.items():
        expected = next(
            (x.expected_layer for x in labels if x.subtype == subtype and x.label == "positive"),
            "-",
        )
        methods: set[str] = set()
        for scenario_id, alert_ids in result.detected.items():
            if by_id[scenario_id].subtype != subtype:
                continue
            for aid in alert_ids:
                found = next((a for a in scored if a.alert_id == aid), None)
                if found:
                    methods.update(found.detection_method)
        tone = "green" if rate >= 0.6 else "yellow" if rate > 0 else "red"
        t.add_row(
            subtype,
            str(total),
            str(got),
            f"[{tone}]{rate:.0%}[/]",
            expected,
            ", ".join(sorted(methods)) or "[red]nothing[/]",
        )
    console.print(t)

    # --- hard negatives, by name ---------------------------------------------------------
    hard_negatives = [x for x in labels if x.label == "hard_negative"]
    t = Table(
        title="\nHard negatives  --  planted legitimate activity shaped to look abusive",
        title_justify="left",
        header_style="bold",
    )
    t.add_column("case")
    t.add_column("what it tests")
    t.add_column("outcome")
    for label in sorted(hard_negatives, key=lambda x: x.scenario_id):
        hits = result.hard_negative_hits.get(label.scenario_id)
        outcome = (
            f"[red]FIRED ({len(hits)} alert{'s' if len(hits) > 1 else ''})[/]"
            if hits
            else "[green]correctly silent[/]"
        )
        t.add_row(label.title, label.notes[:78] + ("..." if len(label.notes) > 78 else ""), outcome)
    console.print(t)

    # --- missed cases ---------------------------------------------------------------------
    missed = [by_id[s] for s, v in result.detected.items() if not v]
    if missed:
        t = Table(
            title="\nMissed cases  --  planted abuse no rule fired on",
            title_justify="left",
            header_style="bold",
        )
        t.add_column("case")
        t.add_column("typology")
        t.add_column("expected layer")
        for label in sorted(missed, key=lambda x: x.scenario_id):
            t.add_row(label.title, label.subtype, label.expected_layer)
        console.print(t)

    # --- sensitivity of the matching rule itself ------------------------------------------
    sweep = jaccard_sweep(scored, labels)
    t = Table(
        title="\nMatching-rule sensitivity  --  does the headline depend on the contract's "
        "0.5 threshold?",
        title_justify="left",
        header_style="bold",
    )
    t.add_column("Jaccard threshold")
    t.add_column("cases detected", justify="right")
    for threshold, (got, total) in sweep.items():
        marker = "  <- contract" if threshold == 0.5 else ""
        t.add_row(f"{threshold:.2f}{marker}", f"{got}/{total}")
    console.print(t)

    console.print(
        "\n[bold]How to read this.[/] Recall is reported per typology because they differ by "
        "an order of\nmagnitude in difficulty -- one aggregate number would mostly measure "
        "whichever typology\nhappens to contribute the most cases. Precision is computed "
        "against every alert raised,\nincluding those on ordinary background activity, "
        "because a compliance team's real\nconstraint is total alert volume rather than the "
        "hit rate on interesting cases.\n"
    )
    return result
