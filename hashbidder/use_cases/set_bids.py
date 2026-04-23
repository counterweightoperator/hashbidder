"""Explicit-bids set-bids use case."""

from hashbidder.clients.braiins import HashpowerClient
from hashbidder.domain.bid_config import SetBidsConfig
from hashbidder.services.bid_runner import SetBidsResult, reconcile


def set_bids(
    client: HashpowerClient, config: SetBidsConfig, dry_run: bool
) -> SetBidsResult:
    """Reconcile live bids against an explicit config."""
    return reconcile(client, config, dry_run)
