"""Explicit-bids set-bids use case."""

from hashbidder.bid_runner import SetBidsResult, reconcile
from hashbidder.client import HashpowerClient
from hashbidder.config import SetBidsConfig


def set_bids(
    client: HashpowerClient, config: SetBidsConfig, dry_run: bool
) -> SetBidsResult:
    """Reconcile live bids against an explicit config."""
    return reconcile(client, config, dry_run)
