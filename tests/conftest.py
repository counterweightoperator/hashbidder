"""Shared test fixtures and helpers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hashbidder.braiins_client import (
    AccountBalance,
    ApiError,
    BidHistory,
    BidId,
    BidStatus,
    ClOrderId,
    CreateBidResult,
    MarketSettings,
    OrderBook,
    Upstream,
    UserBid,
)
from hashbidder.clients.ocean import AccountStats, OceanError
from hashbidder.config import BidConfig, SetBidsConfig
from hashbidder.domain.balance_check import BalanceCheck, BalanceStatus
from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.price_tick import PriceTick
from hashbidder.domain.progress import Progress
from hashbidder.domain.sats import Sats
from hashbidder.domain.sats_burn_rate import SatsBurnRate
from hashbidder.domain.stratum_url import StratumUrl
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.mempool_client import ChainStats, MempoolError

UPSTREAM = Upstream(
    url=StratumUrl("stratum+tcp://pool.example.com:3333"), identity="worker1"
)
OTHER_UPSTREAM = Upstream(
    url=StratumUrl("stratum+tcp://other.pool.com:4444"), identity="worker2"
)

# Canonical hashrate denominators.
PH_DAY = Hashrate(Decimal(1), HashUnit.PH, TimeUnit.DAY)
EH_DAY = Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY)

# Default bid last_updated timestamp used whenever a test doesn't care.
DEFAULT_LAST_UPDATED = datetime(1970, 1, 1, tzinfo=UTC)

# Canned market settings for FakeClient.
DEFAULT_PRICE_TICK = PriceTick(sats=Sats(1000))
DEFAULT_MARKET_SETTINGS = MarketSettings(
    min_bid_price_decrease_period=timedelta(seconds=600),
    min_bid_speed_limit_decrease_period=timedelta(seconds=600),
    price_tick=DEFAULT_PRICE_TICK,
)

DEFAULT_ACCOUNT_BALANCE = AccountBalance(
    available_sat=Sats(10_000_000_000),
    blocked_sat=Sats(0),
    total_sat=Sats(10_000_000_000),
)

SUFFICIENT_BALANCE_CHECK = BalanceCheck(
    required_sat=Sats(0),
    available_sat=DEFAULT_ACCOUNT_BALANCE.available_sat,
    burn_rate=SatsBurnRate.zero(),
    runway=timedelta.max,
    status=BalanceStatus.SUFFICIENT,
)


def make_user_bid(
    bid_id: str,
    price_sat_per_ph_day: int,
    speed: str,
    status: BidStatus = BidStatus.ACTIVE,
    amount: int = 100_000,
    remaining: int | None = None,
    upstream: Upstream | None = None,
    last_updated: datetime = DEFAULT_LAST_UPDATED,
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
        last_updated=last_updated,
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
        market_settings: MarketSettings = DEFAULT_MARKET_SETTINGS,
        account_balance: AccountBalance = DEFAULT_ACCOUNT_BALANCE,
        bid_histories: dict[BidId, BidHistory] | None = None,
    ) -> None:
        """Initialize with optional canned data and error injection."""
        self._orderbook = orderbook or OrderBook(bids=(), asks=())
        self._bids: list[UserBid] = list(current_bids)
        self._next_id = 1
        self._errors = errors or {}
        self._market_settings = market_settings
        self._account_balance = account_balance
        self._bid_histories = dict(bid_histories or {})
        self.calls: list[tuple[str, ...]] = []

    def get_market_settings(self) -> MarketSettings:
        """Return the canned market settings."""
        return self._market_settings

    def get_account_balance(self) -> AccountBalance:
        """Return the canned account balance."""
        return self._account_balance

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
                last_updated=DEFAULT_LAST_UPDATED,
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
                    last_updated=bid.last_updated,
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

    def get_bid_history(self, bid_id: BidId) -> BidHistory:
        """Return seeded history for a bid, or raise ApiError 404."""
        self.calls.append(("get_bid_history", bid_id))
        self._maybe_raise("get_bid_history", bid_id)
        if bid_id not in self._bid_histories:
            raise ApiError(404, f"Bid {bid_id} not found")
        return self._bid_histories[bid_id]


class FakeMempoolSource:
    """In-memory implementation of MempoolSource for testing.

    Supports error injection: set `error` to a MempoolError and all
    calls will raise it.
    """

    def __init__(
        self,
        chain_stats: ChainStats,
        error: MempoolError | None = None,
    ) -> None:
        """Initialize with canned data and optional error."""
        self._chain_stats = chain_stats
        self._error = error

    def get_chain_stats(self, block_count: int) -> ChainStats:
        """Return canned chain stats or raise injected error."""
        if self._error:
            raise self._error
        return self._chain_stats


class FakeOceanSource:
    """In-memory implementation of OceanSource for testing.

    Supports error injection: set `error` to an OceanError and all
    calls will raise it.
    """

    def __init__(
        self,
        account_stats: AccountStats,
        error: OceanError | None = None,
    ) -> None:
        """Initialize with canned data and optional error."""
        self._account_stats = account_stats
        self._error = error

    def get_account_stats(self, address: BtcAddress) -> AccountStats:
        """Return canned account stats or raise injected error."""
        if self._error:
            raise self._error
        return self._account_stats
