"""Bid planning strategy protocol and dispatcher."""

from enum import Enum
from typing import Protocol

from hashbidder.domain.bid_config import BidConfig
from hashbidder.domain.hashrate import Hashrate, HashratePrice
from hashbidder.strategies.naive import plan as naive_plan
from hashbidder.strategies.prioritized_buckets import plan as buckets_plan
from hashbidder.target_hashrate import BidWithCooldown


class BiddingStrategyKind(Enum):
    """Which bid-planning strategy a target-hashrate config selects."""

    NAIVE = "naive"
    PRIORITIZED_BUCKETS = "prioritized-buckets"


class BiddingStrategy(Protocol):
    """Plan bids for a target hashrate given the current bid book."""

    def __call__(
        self,
        *,
        target_price: HashratePrice,
        target_hashrate: Hashrate,
        max_bids_count: int,
        bids: tuple[BidWithCooldown, ...],
    ) -> tuple[BidConfig, ...]:
        """Return the bid configs to reconcile toward."""
        ...


def resolve_strategy(kind: BiddingStrategyKind) -> BiddingStrategy:
    """Return the concrete planner for the given strategy kind."""
    if kind is BiddingStrategyKind.NAIVE:
        return naive_plan
    if kind is BiddingStrategyKind.PRIORITIZED_BUCKETS:
        return buckets_plan
    raise ValueError(f"Unknown bidding strategy: {kind!r}")
