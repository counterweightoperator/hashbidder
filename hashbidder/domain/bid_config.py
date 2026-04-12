"""Desired bid configuration types."""

from dataclasses import dataclass

from hashbidder.domain.hashrate import Hashrate, HashratePrice
from hashbidder.domain.sats import Sats
from hashbidder.domain.upstream import Upstream


@dataclass(frozen=True)
class BidConfig:
    """A single desired bid from the config file."""

    price: HashratePrice
    speed_limit: Hashrate


@dataclass(frozen=True)
class SetBidsConfig:
    """Parsed set-bids configuration (explicit bids mode)."""

    default_amount: Sats
    upstream: Upstream
    bids: tuple[BidConfig, ...]
