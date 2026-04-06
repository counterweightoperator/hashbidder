"""Hashbidder CLI."""

import click
import httpx

from hashbidder import use_cases
from hashbidder.client import API_BASE, BraiinsClient


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Hashbidder CLI."""
    ctx.obj = BraiinsClient(API_BASE)


@cli.command()
@click.pass_obj
def ping(client: BraiinsClient) -> None:
    """Check connectivity to the Braiins Hashpower API.

    Hits the public /spot/orderbook endpoint and prints a summary
    to confirm the API is reachable.
    """
    try:
        book = use_cases.ping(client)
    except httpx.TimeoutException:
        raise click.ClickException("Request timed out.")
    except httpx.HTTPStatusError as e:
        raise click.ClickException(f"HTTP {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        raise click.ClickException(f"Connection error: {e}")

    click.echo(f"OK — order book: {len(book.bids)} bids, {len(book.asks)} asks")


def main() -> None:
    """Entry point for the hashbidder CLI."""
    cli()


if __name__ == "__main__":
    main()
