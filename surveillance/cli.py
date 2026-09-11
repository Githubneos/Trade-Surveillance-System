"""Command-line entry points."""

from __future__ import annotations

import typer
from rich.console import Console

from surveillance.config import get_settings
from surveillance.db.loader import load_dataset
from surveillance.db.session import session_scope
from surveillance.generator.pipeline import GeneratorParams, generate
from surveillance.generator.writer import persist

app = typer.Typer(add_completion=False, help="Trade surveillance system")
console = Console(width=140)


@app.command("generate")
def generate_cmd(
    accounts: int = typer.Option(350, help="Number of accounts"),
    securities: int = typer.Option(60, help="Number of securities"),
    days: int = typer.Option(20, help="Simulated trading days"),
    load: bool = typer.Option(True, help="Load the result into Postgres"),
    stats: bool = typer.Option(True, help="Print the dataset summary"),
) -> None:
    """Generate the synthetic dataset, persist it, and optionally load it into Postgres."""
    settings = get_settings()
    params = GeneratorParams(n_accounts=accounts, n_securities=securities, n_days=days)

    with console.status("generating..."):
        ds = generate(settings, params)
        persist(ds, settings)
    console.print(f"[green]wrote[/] {len(ds.trades):,} trades -> {settings.trades_path}")
    console.print(f"[green]wrote[/] {len(ds.labels)} labels -> {settings.ground_truth_path}")

    if load:
        with console.status("loading into postgres..."), session_scope() as session:
            counts = load_dataset(session, ds)
        console.print(f"[green]loaded[/] {counts}")

    if stats:
        from surveillance.eval.dataset_report import print_report

        print_report(ds, console)


@app.command("report")
def report_cmd() -> None:
    """Print the dataset summary from what is already on disk."""
    import pandas as pd

    from surveillance.eval.dataset_report import print_report
    from surveillance.generator.ground_truth import read_labels
    from surveillance.generator.writer import Dataset

    settings = get_settings()
    ds = Dataset(
        trades=pd.read_parquet(settings.trades_path),
        labels=read_labels(settings.ground_truth_path),
        accounts=[],
        securities=[],
    )
    print_report(ds, console)


@app.command("produce")
def produce_cmd(
    speed: float = typer.Option(
        0.0, help="Simulated seconds per real second (0 = as fast as possible)"
    ),
    limit: int | None = typer.Option(None, help="Only publish the first N trades"),
    reset: bool = typer.Option(False, help="Delete the stream before publishing"),
) -> None:
    """Replay the generated dataset onto the Redis stream."""
    import pandas as pd
    import redis

    from surveillance.stream.producer import publish

    settings = get_settings()
    client = redis.from_url(settings.redis_url)
    if reset:
        client.delete(settings.stream_key)
        console.print("[yellow]stream reset[/]")

    trades = pd.read_parquet(settings.trades_path)
    with console.status("publishing..."):
        stats = publish(
            client, trades, speed=speed, limit=limit, stream_key=settings.stream_key
        )
    rate = stats.published / max(stats.elapsed_s, 1e-9)
    console.print(
        f"[green]published[/] {stats.published:,} trades in {stats.elapsed_s:.1f}s "
        f"({rate:,.0f}/s)"
    )


@app.command("consume")
def consume_cmd(
    name: str = typer.Option("worker-1", help="Consumer name within the group"),
    batch: int = typer.Option(500, help="Messages per batch"),
    idle_exit: float | None = typer.Option(
        5.0, help="Exit after this many idle seconds (None = run forever)"
    ),
    delay_ms: float = typer.Option(0.0, help="Throttle: pause between batches"),
) -> None:
    """Consume the Redis stream into Postgres, idempotently."""
    import redis

    from surveillance.db.session import get_sessionmaker
    from surveillance.stream.consumer import consume

    settings = get_settings()
    client = redis.from_url(settings.redis_url)
    maker = get_sessionmaker()

    console.print(f"[green]consuming[/] as {name}")
    stats = consume(
        client,
        maker,
        consumer_name=name,
        batch_size=batch,
        idle_exit_s=idle_exit,
        stream_key=settings.stream_key,
        group=settings.consumer_group,
        claim_min_idle_ms=settings.claim_min_idle_ms,
        batch_delay_s=delay_ms / 1000.0,
    )
    console.print(
        f"received={stats.received:,} inserted={stats.inserted:,} "
        f"duplicates={stats.duplicates:,} reclaimed={stats.reclaimed:,} "
        f"batches={stats.batches:,}"
    )
    if stats.errors:
        console.print(f"[red]errors:[/] {stats.errors[:3]}")


@app.command("detect")
def detect_cmd(
    persist: bool = typer.Option(True, help="Write alerts to Postgres"),
    report: bool = typer.Option(True, help="Print the scorecard against ground truth"),
) -> None:
    """Run both detection layers, fuse them, and score against ground truth."""
    import pandas as pd

    from surveillance.db.session import session_scope
    from surveillance.detect.persistence import persist_alerts
    from surveillance.detect.pipeline import load_liquidity, run_detection
    from surveillance.eval.report import print_report
    from surveillance.generator.ground_truth import read_labels

    settings = get_settings()
    trades = pd.read_parquet(settings.trades_path)

    with console.status("running detection..."):
        out = run_detection(trades, load_liquidity(settings))
    console.print(
        f"[green]detected[/] {len(out.alerts):,} alerts "
        f"({len(out.typology_alerts):,} per-trade, {len(out.network_alerts):,} network)"
    )

    if persist:
        with console.status("persisting..."), session_scope() as session:
            counts = persist_alerts(session, out.alerts)
        console.print(f"[green]persisted[/] {counts}")

    if report:
        print_report(out.alerts, read_labels(settings.ground_truth_path), trades, console)


@app.command("news")
def news_cmd(
    securities: int = typer.Option(20, help="How many securities to correlate"),
    offline: bool = typer.Option(False, help="Use only cached filings, make no requests"),
) -> None:
    """Correlate trading against real SEC 8-K filings (stretch signal)."""
    import json as _json
    from pathlib import Path

    import pandas as pd

    from surveillance.news.correlation import assign_ciks, correlate
    from surveillance.news.edgar import EdgarClient

    settings = get_settings()
    trades = pd.read_parquet(settings.trades_path)
    reference = _json.loads(settings.reference_path.read_text())
    tickers = {s["id"]: s["ticker"] for s in reference["securities"]}
    chosen = sorted(tickers)[:securities]

    console.print(
        "[yellow]Note:[/] these securities are invented, so they cannot genuinely correlate "
        "with real filings.\nEach is mapped to a real CIK and that company's real 8-K "
        "timestamps are used as news events.\nThis demonstrates the mechanism; it is not a "
        "finding about these instruments."
    )

    client = EdgarClient(cache_dir=Path(settings.data_dir) / "edgar_cache", offline=offline)
    mapping = assign_ciks(chosen)
    filings = {}
    with console.status("fetching filings from EDGAR..."):
        for sid in chosen:
            filings[sid] = client.recent_8k(mapping[sid], limit=40)
    total = sum(len(v) for v in filings.values())
    console.print(f"[green]loaded[/] {total} 8-K filings across {len(chosen)} securities")

    signals = correlate(trades, filings, tickers)
    in_window = len(signals)
    flagged = [s for s in signals if s.flagged]

    from rich.table import Table

    if not in_window:
        console.print(
            "[dim]no filings fell inside the simulated trading window, so there was "
            "nothing to correlate[/]"
        )
        return

    table = Table(
        title=f"\nFilings inside the trading window ({in_window} evaluated)",
        title_justify="left",
        header_style="bold",
    )
    for col in ("ticker", "filed", "event", "materiality", "pre-news", "expected", "lift"):
        table.add_column(
            col, justify="left" if col in ("ticker", "event", "filed") else "right"
        )
    for s in sorted(signals, key=lambda x: -x.activity_lift)[:15]:
        lift = f"{s.activity_lift:.2f}x"
        table.add_row(
            s.ticker,
            f"{s.filed_at:%Y-%m-%d}",
            s.headline[:46],
            f"{s.materiality:.2f}",
            str(s.pre_news_trades),
            f"{s.baseline_trades:.0f}",
            f"[red]{lift}[/]" if s.flagged else lift,
        )
    console.print(table)

    if flagged:
        console.print(
            f"\n[red]{len(flagged)} filing(s) breached both thresholds[/] "
            f"(lift >= 2.0 and materiality >= 0.15)"
        )
    else:
        console.print(
            "\n[green]Nothing breached.[/] Every lift sits near 1.0, which is the correct "
            "result:\nthese trades are synthetic and have no relationship to real filings, "
            "so abnormal\npre-filing accumulation should not exist. The materiality column "
            "shows the\nTF-IDF ranking working -- acquisitions and earnings score high, "
            "routine director\nchanges score zero."
        )


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(8000, help="Port"),
    reload: bool = typer.Option(False, help="Auto-reload on code changes"),
) -> None:
    """Serve the surveillance API and dashboard (no ground-truth access)."""
    import uvicorn

    console.print(f"[green]surveillance API[/] -> http://{host}:{port}")
    uvicorn.run(
        "surveillance.api.app:create_app", host=host, port=port, factory=True, reload=reload
    )


@app.command("explorer")
def explorer_cmd(
    host: str = typer.Option("127.0.0.1", help="Bind address"),
    port: int = typer.Option(8001, help="Port"),
) -> None:
    """Serve the evaluation-side dataset explorer (reads ground truth -- not for prod)."""
    import uvicorn

    console.print(f"[yellow]dataset explorer (reads labels)[/] -> http://{host}:{port}")
    uvicorn.run("surveillance.eval.explorer:create_app", host=host, port=port, factory=True)


if __name__ == "__main__":
    app()
