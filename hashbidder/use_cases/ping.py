"""Ping and current-bids use cases."""

from hashbidder.clients.braiins import HashpowerClient, OrderBook, UserBid


def ping(client: HashpowerClient) -> OrderBook:
    """Fetch the current order book.

    Args:
        client: The hashpower market client to use.

    Returns:
        The current spot order book snapshot.
    """
    return client.get_orderbook()


def get_current_bids(client: HashpowerClient) -> tuple[UserBid, ...]:
    """Fetch the authenticated user's active bids.

    Args:
        client: The hashpower market client to use.

    Returns:
        The user's currently active spot bids.
    """
    return client.get_current_bids()
