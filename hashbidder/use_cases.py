"""Hashbidder use cases."""

from dataclasses import dataclass

from hashbidder.client import BraiinsClient


@dataclass
class OrderBookSummary:
    """Summary of the current spot order book."""

    bids: int
    asks: int


def ping(client: BraiinsClient) -> OrderBookSummary:
    """Fetch and summarize the current order book.

    Args:
        client: The Braiins API client to use.

    Returns:
        A summary with the count of active bids and asks.
    """
    data = client.get_orderbook()
    return OrderBookSummary(
        bids=len(data.bids),
        asks=len(data.asks),
    )
