"""Phase 1 exit report: what was generated, and is the detection problem actually hard?

The second question is the one that matters. A synthetic dataset can trivially produce
excellent precision/recall by planting anomalies that are obvious, so this report measures
the *difficulty* of what was planted rather than only counting it.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

from surveillance.generator.writer import Dataset

Z_THRESHOLD = 3.0
#: One-sided significance level for "this scenario has more size outliers than ordinary
#: background activity". Using a significance test rather than a fixed multiple matters:
#: some scenarios contain only a handful of trades, where a fixed threshold like "3x the
#: baseline" is tripped by a single trade landing in the tail by chance.
CONTAMINATION_ALPHA = 0.01


def binomial_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p). Exact; n here is at most a few thousand."""
    if k <= 0:
        return 1.0
    if p <= 0.0:
        return 0.0
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def account_baselines(trades: pd.DataFrame) -> pd.DataFrame:
    """Per-account log-notional distribution, from background activity only."""
    bg = trades[trades["scenario_id"].isna()].copy()
    bg["notional"] = bg["quantity"] * bg["price"]
    stats = bg.groupby("account_id")["notional"].agg(
        n="size",
        log_mu=lambda s: float(np.log(s).mean()),
        log_sd=lambda s: float(np.log(s).std()),
    )
    return stats[(stats["n"] >= 20) & np.isfinite(stats["log_sd"]) & (stats["log_sd"] > 0)]


def _z(trades: pd.DataFrame, baselines: pd.DataFrame, mask) -> np.ndarray:
    sub = trades[mask].copy()
    if sub.empty:
        return np.array([])
    sub["notional"] = sub["quantity"] * sub["price"]
    j = sub.join(baselines, on="account_id", how="inner")
    if j.empty:
        return np.array([])
    return ((np.log(j["notional"]) - j["log_mu"]) / j["log_sd"]).to_numpy()


def print_report(ds: Dataset, console: Console | None = None) -> None:
    # Wide enough that the scenario table is not ellipsised into uselessness.
    console = console or Console(width=140)
    df = ds.trades
    baselines = account_baselines(df)
    bg_z = _z(df, baselines, df["scenario_id"].isna())
    background_rate = float(np.mean(np.abs(bg_z) > Z_THRESHOLD))

    # --- Volume -----------------------------------------------------------------
    df = df.assign(notional=df["quantity"] * df["price"])
    planted = df["scenario_id"].notna()
    t = Table(title="Dataset", title_justify="left", header_style="bold")
    t.add_column("metric")
    t.add_column("value", justify="right")
    t.add_row("trades", f"{len(df):,}")
    t.add_row("  background", f"{(~planted).sum():,}")
    t.add_row("  planted", f"{planted.sum():,}  ({planted.mean():.2%} of all trades)")
    t.add_row("accounts", f"{df['account_id'].nunique():,}")
    t.add_row("securities", f"{df['security_id'].nunique():,}")
    t.add_row("trading days", f"{df['executed_at'].dt.date.nunique()}")
    t.add_row("window", f"{df['executed_at'].min():%Y-%m-%d} .. {df['executed_at'].max():%Y-%m-%d}")
    t.add_row("median notional", f"${df['notional'].median():,.0f}")
    t.add_row("total notional", f"${df['notional'].sum() / 1e9:,.1f}bn")
    console.print(t)

    # --- Scenarios --------------------------------------------------------------
    by_type: dict[tuple[str, str, str], list] = defaultdict(list)
    for label in ds.labels:
        by_type[(label.label, label.scenario_type, label.subtype)].append(label)

    t = Table(
        title=(
            "Planted scenarios  --  'size outlier rate' is the share of the scenario's "
            f"trades beyond |z|={Z_THRESHOLD:.0f} of\nthe account's OWN background "
            f"distribution. Background baseline: {background_rate:.2%}"
        ),
        title_justify="left",
        header_style="bold",
    )
    t.add_column("label")
    t.add_column("scenario type")
    t.add_column("n", justify="right")
    t.add_column("accts", justify="right")
    t.add_column("trades", justify="right")
    t.add_column("median z", justify="right")
    t.add_column("size sep.", justify="right")
    t.add_column("should be caught by")
    t.add_column("verdict")

    for (label_kind, stype, subtype), labels in sorted(by_type.items(), reverse=True):
        ids = {x.scenario_id for x in labels}
        z = _z(df, baselines, df["scenario_id"].isin(ids))
        n_trades = sum(len(x.trade_external_ids) for x in labels)
        n_accounts = len({a for x in labels for a in x.account_ids})
        rate = float(np.mean(np.abs(z) > Z_THRESHOLD)) if len(z) else float("nan")

        expected = labels[0].expected_layer
        if label_kind == "positive":
            # This column measures ONE feature: notional size relative to the account's own
            # history. A scenario can be perfectly detectable through timing or price and
            # still show near-zero size separability, so the verdict is phrased as a claim
            # about size alone -- never as a claim about detectability in general.
            separable = rate > max(background_rate * 10, 0.5)
            if expected == "graph":
                verdict = (
                    "[green]hidden from size[/]" if not separable
                    else "[red]LEAKS to per-trade layer[/]"
                )
            else:
                verdict = (
                    "[green]size alone suffices[/]" if separable
                    else "[cyan]needs a non-size feature[/]"
                )
        else:
            # Is this scenario's outlier count significantly above the background rate, or
            # is it the handful of tail draws you would expect from this many trades?
            n_out = int(np.sum(np.abs(z) > Z_THRESHOLD)) if len(z) else 0
            pval = binomial_sf(n_out, len(z), background_rate) if len(z) else 1.0
            verdict = (
                f"[red]contaminated[/] (p={pval:.3f})" if pval < CONTAMINATION_ALPHA
                else f"[green]clean[/] (p={pval:.2f})"
            )

        t.add_row(
            "[red]POSITIVE[/]" if label_kind == "positive" else "[blue]hard neg[/]",
            stype if subtype in ("", stype) else f"{stype}\n  \u2514 {subtype}",
            str(len(labels)),
            str(n_accounts),
            f"{n_trades:,}",
            f"{np.median(z):+.2f}" if len(z) else "-",
            f"{rate:.1%}" if len(z) else "-",
            expected,
            verdict,
        )
    console.print(t)

    console.print(
        "\n[bold]Reading this table.[/] 'size sep.' measures ONE feature -- notional relative to\n"
        "the account's own history -- so it speaks only to whether a size-based per-trade rule\n"
        "could separate the scenario. It says nothing about timing or price features.\n\n"
        "The load-bearing rows are the graph positives: 'hidden from size' confirms wash rings\n"
        "and coordinated clusters are invisible to a per-trade size rule, so recall on them will\n"
        "measure the network layer and nothing else. 'LEAKS to per-trade layer' there would mean\n"
        "the graph layer is unmotivated and the headline result is an illusion.\n\n"
        "On hard negatives, 'contaminated' would mean the scenario fires the statistical layer\n"
        "for a reason unrelated to what it tests, making any false positive a generator artefact\n"
        "rather than a finding.\n"
    )
