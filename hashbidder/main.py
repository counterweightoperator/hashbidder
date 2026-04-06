"""Hashbidder CLI."""

import logging
import sys
from pathlib import Path

import click
import httpx

from hashbidder import use_cases
from hashbidder.client import API_BASE, BraiinsClient, HashpowerClient

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
    _setup_logging(verbose, log_file)
    if ctx.obj is None:
        ctx.obj = BraiinsClient(API_BASE)


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


def main() -> None:
    """Entry point for the hashbidder CLI."""
    cli()


if __name__ == "__main__":
    main()
