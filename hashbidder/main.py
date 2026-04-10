"""Hashbidder CLI."""

import contextlib
import logging
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import click
import httpx
from dotenv import load_dotenv

from hashbidder import use_cases
from hashbidder.client import API_BASE, ApiError, BraiinsClient, HashpowerClient
from hashbidder.config import load_config
from hashbidder.domain.hashrate import HashUnit
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.formatting import (
    format_current_bids,
    format_hashvalue,
    format_hashvalue_verbose,
    format_outcome,
    format_plan,
    format_results_summary,
)
from hashbidder.mempool_client import (
    DEFAULT_MEMPOOL_URL,
    MempoolClient,
    MempoolError,
    MempoolSource,
)


@dataclass
class Clients:
    """Shared dependencies for CLI commands."""

    braiins: HashpowerClient | None = field(default=None)
    mempool: MempoolSource | None = field(default=None)


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

logger = logging.getLogger("hashbidder")


@contextlib.contextmanager
def _api_errors() -> Iterator[None]:
    """Translate httpx/ValueError exceptions into ClickExceptions."""
    try:
        yield
    except ApiError as e:
        raise click.ClickException(f"API error: {e.message}") from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    except httpx.TimeoutException:
        raise click.ClickException("Request timed out.")
    except httpx.HTTPStatusError as e:
        raise click.ClickException(
            f"HTTP {e.response.status_code}: {e.response.text}"
        ) from e
    except httpx.RequestError as e:
        raise click.ClickException(f"Connection error: {e}") from e


@contextlib.contextmanager
def _mempool_errors() -> Iterator[None]:
    """Translate mempool/httpx exceptions into ClickExceptions."""
    try:
        yield
    except MempoolError as e:
        raise click.ClickException(f"Mempool error: {e.message}") from e
    except httpx.TimeoutException:
        raise click.ClickException("Request timed out.")
    except httpx.RequestError as e:
        raise click.ClickException(f"Connection error: {e}") from e


def _setup_logging(verbose: bool, log_file: Path | None) -> None:
    """Configure logging for the application.

    Args:
        verbose: If True, set level to DEBUG; otherwise INFO.
        log_file: Optional path to a file to log to in addition to console.
    """
    level = logging.DEBUG if verbose else logging.INFO

    logger.setLevel(level)
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(console)

    if log_file is not None:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(file_handler)


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
@click.option(
    "--log-file",
    type=click.Path(path_type=Path),
    default=None,
    help="Also log to this file.",
)
@click.pass_context
def cli(ctx: click.Context, verbose: bool, log_file: Path | None) -> None:
    """Hashbidder CLI."""
    load_dotenv()
    _setup_logging(verbose, log_file)
    if ctx.obj is None:
        ctx.obj = Clients()
    app: Clients = ctx.obj
    if app.braiins is None:
        api_key = os.environ.get("BRAIINS_API_KEY")
        http_client = httpx.Client(timeout=10.0)
        app.braiins = BraiinsClient(API_BASE, api_key=api_key, http_client=http_client)
    if app.mempool is None:
        env_url = os.environ.get("MEMPOOL_URL")
        mempool_url = httpx.URL(env_url) if env_url else DEFAULT_MEMPOOL_URL
        app.mempool = MempoolClient(mempool_url, httpx.Client(timeout=10.0))


@cli.command()
@click.pass_obj
def ping(app: Clients) -> None:
    """Check connectivity to the Braiins Hashpower API.

    Hits the public /spot/orderbook endpoint and prints a summary
    to confirm the API is reachable.
    """
    assert app.braiins is not None
    logger.debug("Fetching order book")
    with _api_errors():
        book = use_cases.ping(app.braiins)
    logger.debug("Order book: %d bids, %d asks", len(book.bids), len(book.asks))
    click.echo(f"OK — order book: {len(book.bids)} bids, {len(book.asks)} asks")


@cli.command()
@click.pass_obj
def bids(app: Clients) -> None:
    """List your currently active bids."""
    assert app.braiins is not None
    logger.debug("Fetching current bids")
    with _api_errors():
        current_bids = use_cases.get_current_bids(app.braiins)

    if not current_bids:
        click.echo("No active bids.")
        return

    for bid in current_bids:
        price_per_phs = bid.price.to(HashUnit.PH, TimeUnit.DAY)
        click.echo(
            f"{bid.id}  {bid.status.name:>14}  "
            f"price={price_per_phs}  "
            f"limit={bid.speed_limit_ph}  "
            f"remaining={bid.amount_remaining_sat} sat  "
            f"progress={bid.progress}"
        )


@cli.command()
@click.pass_context
def hashvalue(ctx: click.Context) -> None:
    """Compute the current hashvalue (sat/PH/Day) from on-chain data."""
    app: Clients = ctx.obj
    assert app.mempool is not None
    with _mempool_errors():
        components = use_cases.get_hashvalue(app.mempool)

    verbose = ctx.parent is not None and ctx.parent.params.get("verbose", False)
    mempool_url = os.environ.get("MEMPOOL_URL") or str(DEFAULT_MEMPOOL_URL)
    if verbose:
        click.echo(format_hashvalue_verbose(components, mempool_url))
    else:
        click.echo(format_hashvalue(components))


@cli.command("set-bids")
@click.option(
    "--bid-config",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="Path to the TOML bid config file.",
)
@click.option(
    "--dry-run", is_flag=True, help="Print what would change without executing."
)
@click.pass_obj
def set_bids(app: Clients, bid_config: Path, dry_run: bool) -> None:
    """Set bids to match a config file."""
    assert app.braiins is not None
    with _api_errors():
        config = load_config(bid_config)

    with _api_errors():
        result = use_cases.set_bids(app.braiins, config)

    plan = result.plan
    has_changes = plan.edits or plan.creates or plan.cancels

    if dry_run:
        click.echo(format_plan(plan, result.skipped_bids))
        return

    if not has_changes:
        click.echo("No changes needed.")
        return

    click.echo("=== Executing Changes ===")
    exec_result = use_cases.execute_plan(app.braiins, plan)

    for outcome in exec_result.outcomes:
        click.echo(format_outcome(outcome))

    click.echo("")
    click.echo("=== Results ===")
    click.echo(format_results_summary(exec_result.outcomes))

    click.echo("")
    click.echo("=== Current Bids ===")
    click.echo(format_current_bids(exec_result.final_bids))


def main() -> None:
    """Entry point for the hashbidder CLI."""
    cli()


if __name__ == "__main__":
    main()
