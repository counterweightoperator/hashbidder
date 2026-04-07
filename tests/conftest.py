"""Shared test fixtures and helpers."""

from decimal import Decimal

from hashbidder.client import BidStatus, OrderBook, Upstream, UserBid
from hashbidder.config import BidConfig, SetBidsConfig
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.progress import Progress
from hashbidder.domain.sats import Sats
from hashbidder.domain.stratum_url import StratumUrl
from hashbidder.domain.time_unit import TimeUnit

UPSTREAM = Upstream(
    url=StratumUrl("stratum+tcp://pool.example.com:3333"), identity="worker1"
)
OTHER_UPSTREAM = Upstream(
    url=StratumUrl("stratum+tcp://other.pool.com:4444"), identity="worker2"
)

# Canonical hashrate denominators.
PH_DAY = Hashrate(Decimal(1), HashUnit.PH, TimeUnit.DAY)
EH_DAY = Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY)


def make_user_bid(
    bid_id: str,
    price_sat_per_ph_day: int,
    speed: str,
    status: BidStatus = BidStatus.ACTIVE,
    amount: int = 100_000,
    remaining: int | None = None,
    upstream: Upstream | None = None,
) -> UserBid:
    """Build a UserBid for tests.

    Price is specified in sat/PH/Day for convenience. Internally converts
    to sat/EH/Day (the API's native unit) by multiplying by 1000.
    """
    return UserBid(
        id=bid_id,
        price=HashratePrice(sats=Sats(price_sat_per_ph_day * 1000), per=EH_DAY),
        speed_limit_ph=Hashrate(Decimal(speed), HashUnit.PH, TimeUnit.SECOND),
        amount_sat=Sats(amount),
        status=status,
        progress=Progress.from_percentage(Decimal("0")),
        amount_remaining_sat=Sats(remaining if remaining is not None else amount),
        upstream=upstream or UPSTREAM,
    )


def make_bid_config(price: int, speed: str) -> BidConfig:
    """Build a BidConfig for tests."""
    return BidConfig(
        price=HashratePrice(sats=Sats(price), per=PH_DAY),
        speed_limit=Hashrate(Decimal(speed), HashUnit.PH, TimeUnit.SECOND),
    )


def make_config(*bids: BidConfig, upstream: Upstream = UPSTREAM) -> SetBidsConfig:
    """Build a SetBidsConfig for tests."""
    return SetBidsConfig(
        default_amount=Sats(100_000), upstream=upstream, bids=tuple(bids)
    )


class FakeClient:
    """In-memory hashpower client for testing."""

    def __init__(
        self,
        orderbook: OrderBook | None = None,
        current_bids: tuple[UserBid, ...] = (),
    ) -> None:
        """Initialize with canned responses."""
        self._orderbook = orderbook or OrderBook(bids=(), asks=())
        self._current_bids = current_bids

    def get_orderbook(self) -> OrderBook:
        """Return the canned order book."""
        return self._orderbook

    def get_current_bids(self) -> tuple[UserBid, ...]:
        """Return the canned bids."""
        return self._current_bids
