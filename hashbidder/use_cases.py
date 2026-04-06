"""Hashbidder use cases."""

from hashbidder.client import BraiinsClient, OrderBook


def ping(client: BraiinsClient) -> OrderBook:
    """Fetch the current order book.

    Args:
        client: The Braiins API client to use.

    Returns:
        The current spot order book snapshot.
    """
    return client.get_orderbook()
