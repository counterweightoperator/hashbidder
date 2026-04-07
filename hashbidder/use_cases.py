"""Hashbidder use cases."""

from dataclasses import dataclass

from hashbidder.client import HashpowerClient, OrderBook, UserBid
from hashbidder.config import SetBidsConfig
from hashbidder.reconcile import MANAGEABLE_STATUSES, ReconciliationPlan, reconcile


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


@dataclass(frozen=True)
class SetBidsResult:
    """Result of the set-bids reconciliation."""

    plan: ReconciliationPlan
    skipped_bids: tuple[UserBid, ...]


def set_bids(client: HashpowerClient, config: SetBidsConfig) -> SetBidsResult:
    """Reconcile current bids against the desired config.

    Args:
        client: The hashpower market client to use.
        config: The desired bid configuration.

    Returns:
        The reconciliation plan and any skipped (non-manageable) bids.
    """
    current_bids = client.get_current_bids()
    plan = reconcile(config, current_bids)
    skipped = tuple(b for b in current_bids if b.status not in MANAGEABLE_STATUSES)
    return SetBidsResult(plan=plan, skipped_bids=skipped)
