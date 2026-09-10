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


if __name__ == "__main__":
    app()
