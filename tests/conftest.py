"""Shared test fixtures and helpers."""

from decimal import Decimal

from hashbidder.client import (
    ApiError,
    BidId,
    BidStatus,
    ClOrderId,
    CreateBidResult,
    OrderBook,
    Upstream,
    UserBid,
)
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
        id=BidId(bid_id),
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
    """Stateful in-memory hashpower client for testing.

    Tracks bids as mutable state so that create/edit/cancel mutations
    are reflected in subsequent get_current_bids() calls.

    Supports error injection via the `errors` dict: map
    (method_name, bid_id) to a list of ApiError. Each call pops the
    next error; when the list is exhausted the real logic runs.
    """

    def __init__(
        self,
        orderbook: OrderBook | None = None,
        current_bids: tuple[UserBid, ...] = (),
        errors: dict[tuple[str, str], list[ApiError]] | None = None,
    ) -> None:
        """Initialize with optional canned data and error injection."""
        self._orderbook = orderbook or OrderBook(bids=(), asks=())
        self._bids: list[UserBid] = list(current_bids)
        self._next_id = 1
        self._errors = errors or {}
        self.calls: list[tuple[str, ...]] = []

    def _maybe_raise(self, method: str, key: str) -> None:
        errs = self._errors.get((method, key))
        if errs:
            raise errs.pop(0)

    def get_orderbook(self) -> OrderBook:
        """Return the canned order book."""
        return self._orderbook

    def get_current_bids(self) -> tuple[UserBid, ...]:
        """Return current bids reflecting any mutations."""
        return tuple(self._bids)

    def create_bid(
        self,
        upstream: Upstream,
        amount_sat: Sats,
        price: HashratePrice,
        speed_limit: Hashrate,
        cl_order_id: ClOrderId,
    ) -> CreateBidResult:
        """Create a bid, appending it to internal state."""
        self.calls.append(("create_bid", cl_order_id))
        self._maybe_raise("create_bid", cl_order_id)
        bid_id = BidId(f"B{self._next_id:09d}")
        self._next_id += 1
        self._bids.append(
            UserBid(
                id=bid_id,
                price=price,
                speed_limit_ph=speed_limit,
                amount_sat=amount_sat,
                status=BidStatus.CREATED,
                progress=Progress.from_percentage(Decimal("0")),
                amount_remaining_sat=amount_sat,
                upstream=upstream,
            )
        )
        return CreateBidResult(id=bid_id)

    def edit_bid(
        self,
        bid_id: BidId,
        new_price: HashratePrice,
        new_speed_limit: Hashrate,
    ) -> None:
        """Edit a bid, replacing its price and speed limit."""
        self.calls.append(("edit_bid", bid_id))
        self._maybe_raise("edit_bid", bid_id)
        for i, bid in enumerate(self._bids):
            if bid.id == bid_id:
                self._bids[i] = UserBid(
                    id=bid.id,
                    price=new_price,
                    speed_limit_ph=new_speed_limit,
                    amount_sat=bid.amount_sat,
                    status=bid.status,
                    progress=bid.progress,
                    amount_remaining_sat=bid.amount_remaining_sat,
                    upstream=bid.upstream,
                )
                return
        raise ApiError(404, f"Bid {bid_id} not found")

    def cancel_bid(self, order_id: BidId) -> None:
        """Cancel a bid, removing it from internal state."""
        self.calls.append(("cancel_bid", order_id))
        self._maybe_raise("cancel_bid", order_id)
        for i, bid in enumerate(self._bids):
            if bid.id == order_id:
                del self._bids[i]
                return
        raise ApiError(404, f"Bid {order_id} not found")
