"""User spot market bid types."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import NewType

from hashbidder.domain.hashrate import Hashrate, HashratePrice
from hashbidder.domain.progress import Progress
from hashbidder.domain.sats import Sats
from hashbidder.domain.upstream import Upstream

BidId = NewType("BidId", str)


class BidStatus(Enum):
    """Status of a user's spot bid."""

    UNSPECIFIED = "BID_STATUS_UNSPECIFIED"
    ACTIVE = "BID_STATUS_ACTIVE"
    PENDING_CANCEL = "BID_STATUS_PENDING_CANCEL"
    CANCELED = "BID_STATUS_CANCELED"
    FULFILLED = "BID_STATUS_FULFILLED"
    PAUSED = "BID_STATUS_PAUSED"
    FROZEN = "BID_STATUS_FROZEN"
    CREATED = "BID_STATUS_CREATED"


@dataclass(frozen=True)
class UserBid:
    """A user's spot market bid."""

    id: BidId
    price: HashratePrice
    speed_limit_ph: Hashrate
    amount_sat: Sats
    status: BidStatus
    progress: Progress | None
    amount_remaining_sat: Sats | None
    last_updated: datetime
    upstream: Upstream | None
