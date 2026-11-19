"""Command-line interface: ``switchboard serve | db upgrade | keys ...``."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

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

app = typer.Typer(help="Switchboard LLM gateway.", no_args_is_help=True)
keys_app = typer.Typer(help="Manage API keys.", no_args_is_help=True)
db_app = typer.Typer(help="Database migrations.", no_args_is_help=True)
app.add_typer(keys_app, name="keys")
app.add_typer(db_app, name="db")

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
