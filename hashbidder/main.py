"""Hashbidder CLI."""

import logging
import os
import sys
from pathlib import Path

import click
import httpx
from dotenv import load_dotenv

from hashbidder import use_cases
from hashbidder.client import API_BASE, BraiinsClient, HashpowerClient
from hashbidder.config import load_config
from hashbidder.domain.hashrate import HashUnit
from hashbidder.domain.time_unit import TimeUnit

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

logger = logging.getLogger("hashbidder")


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
        api_key = os.environ.get("BRAIINS_API_KEY")
        ctx.obj = BraiinsClient(API_BASE, api_key=api_key)


@cli.command()
@click.pass_obj
def ping(client: HashpowerClient) -> None:
    """Check connectivity to the Braiins Hashpower API.

    Hits the public /spot/orderbook endpoint and prints a summary
    to confirm the API is reachable.
    """
    logger.debug("Fetching order book")
    try:
        book = use_cases.ping(client)
    except httpx.TimeoutException:
        raise click.ClickException("Request timed out.")
    except httpx.HTTPStatusError as e:
        raise click.ClickException(f"HTTP {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        raise click.ClickException(f"Connection error: {e}")

    logger.debug("Order book: %d bids, %d asks", len(book.bids), len(book.asks))
    click.echo(f"OK — order book: {len(book.bids)} bids, {len(book.asks)} asks")


@cli.command()
@click.pass_obj
def bids(client: HashpowerClient) -> None:
    """List your currently active bids."""
    logger.debug("Fetching current bids")
    try:
        current_bids = use_cases.get_current_bids(client)
    except ValueError as e:
        raise click.ClickException(str(e))
    except httpx.TimeoutException:
        raise click.ClickException("Request timed out.")
    except httpx.HTTPStatusError as e:
        raise click.ClickException(f"HTTP {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        raise click.ClickException(f"Connection error: {e}")

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
def set_bids(client: HashpowerClient, bid_config: Path, dry_run: bool) -> None:
    """Set bids to match a config file."""
    try:
        config = load_config(bid_config)
    except ValueError as e:
        raise click.ClickException(str(e))

    click.echo(f"Loaded config: {len(config.bids)} bid(s)")
    click.echo(f"  default_amount: {config.default_amount} sat")
    click.echo(f"  upstream: {config.upstream.url} / {config.upstream.identity}")
    for i, bid in enumerate(config.bids):
        click.echo(f"  bid {i}: price={bid.price}, speed_limit={bid.speed_limit}")

    if not dry_run:
        raise NotImplementedError("Only --dry-run is supported for now.")


def main() -> None:
    """Entry point for the hashbidder CLI."""
    cli()


if __name__ == "__main__":
    main()
