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
from hashbidder.config import SetBidsConfig, TargetHashrateConfig, load_config
from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import HashUnit
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.formatting import (
    format_hashvalue,
    format_hashvalue_verbose,
    format_ocean_stats,
    format_set_bids_result,
    format_set_bids_target_result,
    format_set_bids_target_result_verbose,
)
from hashbidder.mempool_client import (
    DEFAULT_MEMPOOL_URL,
    MempoolClient,
    MempoolError,
    MempoolSource,
)
from hashbidder.ocean_client import (
    DEFAULT_OCEAN_URL,
    OceanClient,
    OceanError,
    OceanSource,
)


@dataclass
class Clients:
    """Shared dependencies for CLI commands."""

    braiins: HashpowerClient | None = field(default=None)
    mempool: MempoolSource | None = field(default=None)
    ocean: OceanSource | None = field(default=None)


def _resolve_mempool_url() -> httpx.URL:
    """Resolve the mempool URL from env, falling back to the default."""
    env_url = os.environ.get("MEMPOOL_URL")
    return httpx.URL(env_url) if env_url else DEFAULT_MEMPOOL_URL


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
    except httpx.TimeoutException as e:
        raise click.ClickException("Request timed out.") from e
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
    except httpx.TimeoutException as e:
        raise click.ClickException("Request timed out.") from e
    except httpx.RequestError as e:
        raise click.ClickException(f"Connection error: {e}") from e


@contextlib.contextmanager
def _ocean_errors() -> Iterator[None]:
    """Translate Ocean/httpx exceptions into ClickExceptions."""
    try:
        yield
    except OceanError as e:
        raise click.ClickException(f"Ocean error: {e.message}") from e
    except httpx.TimeoutException as e:
        raise click.ClickException("Request timed out.") from e
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
        app.mempool = MempoolClient(_resolve_mempool_url(), httpx.Client(timeout=10.0))
    if app.ocean is None:
        app.ocean = OceanClient(DEFAULT_OCEAN_URL, httpx.Client(timeout=10.0))


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

    verbose = ctx.find_root().params["verbose"]
    if verbose:
        click.echo(format_hashvalue_verbose(components, _resolve_mempool_url()))
    else:
        click.echo(format_hashvalue(components))


@cli.command("ocean-account-stats")
@click.pass_context
def ocean_account_stats(ctx: click.Context) -> None:
    """Fetch Ocean hashrate stats for a Bitcoin mining address."""
    app: Clients = ctx.obj
    assert app.ocean is not None
    address_str = os.environ.get("OCEAN_ADDRESS")
    if not address_str:
        click.echo("Error: OCEAN_ADDRESS environment variable is required.", err=True)
        ctx.exit(1)
        return
    try:
        address = BtcAddress(address_str)
    except ValueError as e:
        click.echo(f"Error: invalid OCEAN_ADDRESS: {e}", err=True)
        ctx.exit(1)
        return
    with _ocean_errors():
        stats = use_cases.get_ocean_account_stats(app.ocean, address)
    click.echo(format_ocean_stats(stats, address))


def _resolve_ocean_address(ctx: click.Context) -> BtcAddress:
    """Read and parse OCEAN_ADDRESS from the environment, or exit with error."""
    address_str = os.environ.get("OCEAN_ADDRESS")
    if not address_str:
        raise click.ClickException("OCEAN_ADDRESS environment variable is required.")
    try:
        return BtcAddress(address_str)
    except ValueError as e:
        raise click.ClickException(f"invalid OCEAN_ADDRESS: {e}") from e


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
@click.pass_context
def set_bids(ctx: click.Context, bid_config: Path, dry_run: bool) -> None:
    """Set bids to match a config file."""
    app: Clients = ctx.obj
    assert app.braiins is not None
    assert app.ocean is not None
    with _api_errors():
        config = load_config(bid_config)

    if isinstance(config, TargetHashrateConfig):
        address = _resolve_ocean_address(ctx)
        with _api_errors(), _ocean_errors():
            target_result = use_cases.set_bids_target(
                app.braiins, app.ocean, address, config, dry_run
            )
        verbose = ctx.find_root().params["verbose"]
        if verbose:
            click.echo(format_set_bids_target_result_verbose(target_result))
        else:
            click.echo(format_set_bids_target_result(target_result))
        return

    assert isinstance(config, SetBidsConfig)
    with _api_errors():
        result = use_cases.set_bids(app.braiins, config, dry_run)
    click.echo(format_set_bids_result(result))


def main() -> None:
    """Entry point for the hashbidder CLI."""
    cli()


if __name__ == "__main__":
    main()
