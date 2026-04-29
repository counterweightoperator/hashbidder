"""Desired bid configuration types."""

from dataclasses import dataclass
from decimal import Decimal

from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.domain.upstream import Upstream

# A bid's speed limit must be at least 1 PH/s — bids below this floor are
# rejected by the upstream API.
MIN_BID_SPEED_LIMIT: Hashrate = Hashrate(Decimal(1), HashUnit.PH, TimeUnit.SECOND)


@dataclass(frozen=True)
class BidConfig:
    """A single desired bid from the config file."""

    price: HashratePrice
    speed_limit: Hashrate

    def __post_init__(self) -> None:
        if self.speed_limit < MIN_BID_SPEED_LIMIT:
            raise ValueError(
                f"BidConfig.speed_limit must be >= {MIN_BID_SPEED_LIMIT}, "
                f"got {self.speed_limit}"
            )


@dataclass(frozen=True)
class SetBidsConfig:
    """Parsed set-bids configuration (explicit bids mode)."""

    default_amount: Sats
    upstream: Upstream
    bids: tuple[BidConfig, ...]


@dataclass(frozen=True)
class TargetHashrateConfig:
    """Parsed set-bids configuration for target-hashrate mode."""

    default_amount: Sats
    upstream: Upstream
    target_hashrate: Hashrate
