"""Desired bid configuration types."""

from dataclasses import dataclass
from decimal import Decimal

from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.domain.upstream import Upstream

MIN_BID_SPEED = Hashrate(Decimal(1), HashUnit.PH, TimeUnit.SECOND)
"""Minimum speed per bid accepted by the Braiins platform."""


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
