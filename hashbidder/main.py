"""Hashbidder CLI."""

import click
import httpx

API_BASE = "https://hashpower.braiins.com/v1"


@click.group()
def cli() -> None:
    """Hashbidder CLI."""

@cli.command()
def ping() -> None:
    """Check connectivity to the Braiins Hashpower API.

    Hits the public /spot/orderbook endpoint and prints a summary
    to confirm the API is reachable.
    """
    try:
        response = httpx.get(f"{API_BASE}/spot/orderbook", timeout=10)
        response.raise_for_status()
    except httpx.TimeoutException:
        raise click.ClickException("Request timed out.")
    except httpx.HTTPStatusError as e:
        raise click.ClickException(f"HTTP {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        raise click.ClickException(f"Connection error: {e}")

    data = response.json()
    bids = len(data.get("bids", []))
    asks = len(data.get("asks", []))
    click.echo(f"OK — order book: {bids} bids, {asks} asks")


if __name__ == "__main__":
    cli()
