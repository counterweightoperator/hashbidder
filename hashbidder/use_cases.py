"""Hashbidder use cases."""

from hashbidder.client import HashpowerClient, OrderBook


def ping(client: HashpowerClient) -> OrderBook:
    """Fetch the current order book.

    Args:
        client: The hashpower market client to use.

    Returns:
        The current spot order book snapshot.
    """
    return client.get_orderbook()
