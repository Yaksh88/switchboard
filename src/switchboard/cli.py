"""Command-line interface: ``switchboard serve | db upgrade | keys ...``."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

import httpx
import typer
import uvicorn
from alembic import command
from alembic.config import Config
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from switchboard.auth.keys import KeyStore
from switchboard.config import Settings
from switchboard.db.engine import create_engine, session_factory
from switchboard.evals.report import compare, render_markdown, render_table
from switchboard.evals.runner import EvalRunner, RunResult
from switchboard.evals.suite import load_suite

app = typer.Typer(help="Switchboard LLM gateway.", no_args_is_help=True)
keys_app = typer.Typer(help="Manage API keys.", no_args_is_help=True)
db_app = typer.Typer(help="Database migrations.", no_args_is_help=True)
eval_app = typer.Typer(help="Run eval suites against a gateway.", no_args_is_help=True)
app.add_typer(keys_app, name="keys")
app.add_typer(db_app, name="db")
app.add_typer(eval_app, name="eval")

console = Console()
T = TypeVar("T")


def _alembic_config() -> Config:
    # Works from the repo root and from a container whose WORKDIR holds alembic.ini.
    for candidate in (Path.cwd(), Path(__file__).resolve().parents[2]):
        if (candidate / "alembic.ini").exists():
            config = Config(str(candidate / "alembic.ini"))
            config.set_main_option("script_location", str(candidate / "migrations"))
            return config
    raise typer.BadParameter("alembic.ini not found; run from the project root")


def _with_store(action: Callable[[KeyStore], Awaitable[T]]) -> T:
    url = Settings().database_url
    if not url:
        console.print("[red]SWITCHBOARD_DATABASE_URL is not set[/red]")
        raise typer.Exit(1)

    async def run() -> T:
        engine = create_engine(url)
        try:
            return await action(KeyStore(session_factory(engine)))
        finally:
            await engine.dispose()

    return asyncio.run(run())


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Defaults to SWITCHBOARD_HOST."),
    port: int | None = typer.Option(None, help="Defaults to SWITCHBOARD_PORT."),
) -> None:
    """Run the gateway."""
    settings = Settings()
    uvicorn.run(
        "switchboard.api.app:create_app",
        factory=True,
        host=host or settings.host,
        port=port or settings.port,
        log_level=settings.log_level,
    )


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    """Apply migrations."""
    command.upgrade(_alembic_config(), revision)
    console.print(f"[green]Database is at {revision}[/green]")


@keys_app.command("create")
def keys_create(
    name: str = typer.Option(..., "--name", help="Label shown in listings."),
    rpm: int | None = typer.Option(None, "--rpm", help="Requests per minute."),
    tpm: int | None = typer.Option(None, "--tpm", help="Tokens per minute."),
    budget: float | None = typer.Option(None, "--budget", help="Monthly budget in USD."),
    model: list[str] | None = typer.Option(None, "--model", help="Allowed model; repeatable."),
) -> None:
    """Create a key. The secret is shown once."""
    key, raw = _with_store(
        lambda store: store.create(
            name,
            rpm_limit=rpm,
            tpm_limit=tpm,
            monthly_budget_usd=budget,
            allowed_models=model or None,
        )
    )
    console.print(
        Panel(
            f"[bold]{raw}[/bold]\n\nid: {key.id}\nStore this now; it can't be shown again.",
            title=f"API key '{name}'",
            border_style="green",
        )
    )


@keys_app.command("list")
def keys_list() -> None:
    """List keys (never the secrets)."""
    keys = _with_store(lambda store: store.list())
    table = Table("id", "name", "prefix", "rpm", "tpm", "budget $", "models", "status")
    for key in keys:
        table.add_row(
            str(key.id),
            key.name,
            key.key_prefix,
            str(key.rpm_limit or "-"),
            str(key.tpm_limit or "-"),
            str(key.monthly_budget_usd or "-"),
            ", ".join(key.allowed_models) if key.allowed_models else "all",
            "revoked" if key.revoked_at else "active",
        )
    console.print(table)


@keys_app.command("revoke")
def keys_revoke(key_id: uuid.UUID) -> None:
    """Revoke a key."""
    if not _with_store(lambda store: store.revoke(key_id)):
        console.print("[red]No active key with that id[/red]")
        raise typer.Exit(1)
    console.print("[green]Revoked[/green]")


def _load_baseline(baseline: Path | None, suite_name: str) -> RunResult | None:
    """``baseline`` is a result file, or a directory holding ``<suite>.json`` files."""
    if baseline is None:
        return None
    path = baseline / f"{suite_name}.json" if baseline.is_dir() else baseline
    if not path.exists():
        console.print(
            f"[yellow]No baseline for '{suite_name}' at {path}; skipping comparison[/yellow]"
        )
        return None
    return RunResult.from_json(json.loads(path.read_text()))


def _report_to_gateway(result: RunResult, base_url: str) -> None:
    token = os.environ.get("SWITCHBOARD_ADMIN_TOKEN")
    if not token:
        console.print("[red]--report needs SWITCHBOARD_ADMIN_TOKEN[/red]")
        raise typer.Exit(2)
    response = httpx.post(
        f"{base_url.rstrip('/')}/admin/api/evals",
        json=result.to_json(),
        headers={"authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()


@eval_app.command("run")
def eval_run(
    suites: list[Path] = typer.Argument(..., exists=True, dir_okay=False, help="Suite YAML files."),
    model: str | None = typer.Option(None, help="Override the model named in each suite."),
    base_url: str = typer.Option("http://localhost:8080", envvar="SWITCHBOARD_URL"),
    api_key: str | None = typer.Option(None, envvar="SWITCHBOARD_API_KEY"),
    concurrency: int = typer.Option(8, min=1),
    out: Path = typer.Option(Path("eval-results"), help="Where to write <suite>.json results."),
    baseline: Path | None = typer.Option(None, help="Earlier result file, or a directory of them."),
    max_regression: float = typer.Option(
        0.05, help="Largest tolerated pass-rate drop vs baseline."
    ),
    min_pass_rate: float = typer.Option(0.0, help="Fail if a suite's pass rate is below this."),
    summary_md: Path | None = typer.Option(
        None, help="Append a Markdown summary (CI job summary)."
    ),
    report: bool = typer.Option(False, "--report", help="Upload results to the gateway dashboard."),
) -> None:
    """Run suites and exit 1 on regression, a failed critical case, or a low pass rate."""
    failed = False
    out.mkdir(parents=True, exist_ok=True)

    async def run_all() -> list[RunResult]:
        async with EvalRunner(base_url, api_key, concurrency=concurrency) as runner:
            return [await runner.run(load_suite(path), model) for path in suites]

    for result in asyncio.run(run_all()):
        previous = _load_baseline(baseline, result.suite)
        comparison = compare(result, previous) if previous else None

        (out / f"{result.suite}.json").write_text(json.dumps(result.to_json(), indent=2))
        console.print(render_table(result, comparison))
        if summary_md is not None:
            with summary_md.open("a") as handle:
                handle.write(render_markdown(result, comparison) + "\n")
        if report:
            _report_to_gateway(result, base_url)

        critical = [c.case_id for c in result.cases if c.critical and not c.passed]
        problems = []
        if comparison is not None and comparison.exceeds_drop(max_regression):
            problems.append(
                f"pass rate fell from {comparison.baseline_pass_rate:.0%} to "
                f"{comparison.current_pass_rate:.0%} (limit {max_regression:.0%})"
            )
        if critical:
            problems.append(f"critical cases failed: {', '.join(critical)}")
        if result.pass_rate < min_pass_rate:
            problems.append(f"pass rate {result.pass_rate:.0%} is below {min_pass_rate:.0%}")
        for problem in problems:
            console.print(f"[red]{result.suite}: {problem}[/red]")
        failed = failed or bool(problems)

    raise typer.Exit(1 if failed else 0)
