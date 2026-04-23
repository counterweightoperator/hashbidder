"""Braiins Hashpower API client."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, NewType, Protocol
from urllib.parse import unquote

import httpx

from hashbidder.domain.bid_history import BidHistory, BidHistoryEntry
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.price_tick import PriceTick
from hashbidder.domain.progress import Progress
from hashbidder.domain.sats import Sats
from hashbidder.domain.stratum_url import StratumUrl
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.domain.upstream import Upstream
from hashbidder.domain.user_bid import BidId, BidStatus, UserBid

__all__ = [
    "API_BASE",
    "AccountBalance",
    "ApiError",
    "AskItem",
    "BidHistory",
    "BidHistoryEntry",
    "BidId",
    "BidItem",
    "BidStatus",
    "BraiinsClient",
    "ClOrderId",
    "CreateBidResult",
    "HashpowerClient",
    "MarketSettings",
    "OrderBook",
    "Upstream",
    "UserBid",
]

logger = logging.getLogger(__name__)

API_BASE = httpx.URL("https://hashpower.braiins.com/v1")

ClOrderId = NewType("ClOrderId", str)


class ApiError(Exception):
    """An error returned by the Braiins API."""

    def __init__(self, status_code: int, message: str) -> None:
        """Initialize with the HTTP status code and decoded error message."""
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code}: {message}")

    @property
    def is_transient(self) -> bool:
        """Whether this error is worth retrying (429 or 5xx)."""
        return self.status_code == 429 or self.status_code >= 500


@dataclass
class BidItem:
    """A single bid level in the order book."""

    price: HashratePrice
    amount_sat: Sats
    hr_matched_ph: Hashrate
    speed_limit_ph: Hashrate


@dataclass
class AskItem:
    """A single ask level in the order book."""

    price: HashratePrice
    hr_matched_ph: Hashrate
    hr_available_ph: Hashrate


@dataclass
class OrderBook:
    """Snapshot of the spot market order book."""

    bids: tuple[BidItem, ...]
    asks: tuple[AskItem, ...]


@dataclass(frozen=True)
class MarketSettings:
    """Spot market settings fetched from /spot/settings."""

    min_bid_price_decrease_period: timedelta
    min_bid_speed_limit_decrease_period: timedelta
    price_tick: PriceTick


@dataclass(frozen=True)
class AccountBalance:
    """The authenticated account's balance."""

    available_sat: Sats
    blocked_sat: Sats
    total_sat: Sats


@dataclass(frozen=True)
class CreateBidResult:
    """Result of creating a new bid."""

    id: BidId


class HashpowerClient(Protocol):
    """Protocol for hashpower market clients."""

    def get_orderbook(self) -> OrderBook:
        """Fetch the current spot order book."""
        ...

    def get_current_bids(self) -> tuple[UserBid, ...]:
        """Fetch the authenticated user's active bids."""
        ...

    def create_bid(
        self,
        upstream: Upstream,
        amount_sat: Sats,
        price: HashratePrice,
        speed_limit: Hashrate,
        cl_order_id: ClOrderId,
    ) -> CreateBidResult:
        """Create a new spot bid."""
        ...

    def edit_bid(
        self,
        bid_id: BidId,
        new_price: HashratePrice,
        new_speed_limit: Hashrate,
    ) -> None:
        """Edit an existing spot bid's price and speed limit."""
        ...

    def cancel_bid(self, order_id: BidId) -> None:
        """Cancel an existing spot bid."""
        ...

    def get_market_settings(self) -> MarketSettings:
        """Fetch the current spot market settings."""
        ...

    def get_account_balance(self) -> AccountBalance:
        """Fetch the authenticated account's balance."""
        ...

    def get_bid_history(self, bid_id: BidId) -> BidHistory:
        """Fetch a bid's history as a BidHistory."""
        ...


def _parse_user_bid(item: dict[str, Any]) -> UserBid:
    bid = item["bid"]
    state = item.get("state_estimate")
    upstream = bid.get("dest_upstream")
    return UserBid(
        id=BidId(bid["id"]),
        price=HashratePrice(
            sats=Sats(int(bid["price_sat"])),
            per=Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY),
        ),
        speed_limit_ph=Hashrate(
            Decimal(bid["speed_limit_ph"]), HashUnit.PH, TimeUnit.SECOND
        ),
        amount_sat=Sats(int(bid["amount_sat"])),
        status=BidStatus(bid["status"]),
        progress=Progress.from_percentage(Decimal(state["progress_pct"]))
        if state is not None
        else None,
        amount_remaining_sat=Sats(int(state["amount_remaining_sat"]))
        if state is not None
        else None,
        last_updated=datetime.fromisoformat(bid["last_updated"]),
        upstream=Upstream(
            url=StratumUrl(upstream["url"]),
            identity=upstream["identity"],
        )
        if upstream is not None
        else None,
    )


class BraiinsClient:
    """HTTP client for the Braiins Hashpower API."""

    _SPOT_ORDERBOOK_PATH = "/spot/orderbook"
    _SPOT_BID_CURRENT_PATH = "/spot/bid/current"
    _SPOT_BID_PATH = "/spot/bid"
    _SPOT_BID_DETAIL_PATH = "/spot/bid/detail"
    _SPOT_SETTINGS_PATH = "/spot/settings"
    _ACCOUNT_BALANCE_PATH = "/account/balance"

    # API wire units.
    _API_HASH_UNIT = HashUnit.EH
    _API_TIME_UNIT = TimeUnit.DAY
    _API_SPEED_HASH_UNIT = HashUnit.PH
    _API_SPEED_TIME_UNIT = TimeUnit.SECOND

    def __init__(
        self,
        base_url: httpx.URL,
        api_key: str | None,
        http_client: httpx.Client,
    ) -> None:
        """Initialize the client.

        Args:
            base_url: The base URL of the Braiins Hashpower API.
            api_key: API token for authenticated endpoints.
            http_client: The httpx.Client to use for requests.
        """
        self._base_url = base_url
        self._api_key = api_key
        self._http = http_client

    def _auth_headers(self) -> dict[str, str]:
        """Return authentication headers.

        Raises:
            ValueError: If no API key is configured.
        """
        if self._api_key is None:
            raise ValueError("API key required for authenticated endpoints")
        return {"apikey": self._api_key}

    @staticmethod
    def _raise_api_error(response: httpx.Response) -> None:
        """Raise an ApiError from a non-2xx response."""
        grpc_msg = response.headers.get("grpc-message", "")
        if grpc_msg:
            message = unquote(grpc_msg)
        else:
            # Try JSON body with a "message" field.
            try:
                data = response.json()
                message = data["message"]
            except (json.JSONDecodeError, KeyError, TypeError):
                message = response.text or response.reason_phrase or "Unknown error"
        raise ApiError(response.status_code, message)

    def _price_to_api_sats(self, price: HashratePrice) -> int:
        """Convert a HashratePrice to the API's sat amount in wire units."""
        return price.to(self._API_HASH_UNIT, self._API_TIME_UNIT).sats

    def _speed_to_api_value(self, speed: Hashrate) -> float:
        """Convert a Hashrate to the API's speed limit float in PH/s."""
        return float(
            speed.to(self._API_SPEED_HASH_UNIT, self._API_SPEED_TIME_UNIT).value
        )

    def get_orderbook(self) -> OrderBook:
        """Fetch the current spot order book.

        Returns:
            A structured snapshot of the order book with bids and asks.

        Raises:
            httpx.TimeoutException: If the request times out.
            httpx.HTTPStatusError: If the server returns an error status.
            httpx.RequestError: If a network-level error occurs.
        """
        url = f"{self._base_url}{self._SPOT_ORDERBOOK_PATH}"
        logger.debug("GET %s", url)
        response = self._http.get(url)
        response.raise_for_status()
        logger.debug("Response %s (%d bytes)", response.status_code, len(response.text))
        data: dict[str, list[dict[str, Any]]] = json.loads(
            response.text, parse_float=Decimal
        )
        return OrderBook(
            bids=tuple(
                BidItem(
                    price=HashratePrice(
                        sats=Sats(int(item["price_sat"])),
                        per=Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY),
                    ),
                    amount_sat=Sats(int(item["amount_sat"])),
                    hr_matched_ph=Hashrate(
                        Decimal(item["hr_matched_ph"]), HashUnit.PH, TimeUnit.SECOND
                    ),
                    speed_limit_ph=Hashrate(
                        Decimal(item["speed_limit_ph"]), HashUnit.PH, TimeUnit.SECOND
                    ),
                )
                for item in data["bids"]
            ),
            asks=tuple(
                AskItem(
                    price=HashratePrice(
                        sats=Sats(int(item["price_sat"])),
                        per=Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY),
                    ),
                    hr_matched_ph=Hashrate(
                        Decimal(item["hr_matched_ph"]), HashUnit.PH, TimeUnit.SECOND
                    ),
                    hr_available_ph=Hashrate(
                        Decimal(item["hr_available_ph"]), HashUnit.PH, TimeUnit.SECOND
                    ),
                )
                for item in data["asks"]
            ),
        )

    def get_current_bids(self) -> tuple[UserBid, ...]:
        """Fetch the authenticated user's active bids.

        Returns:
            The user's currently active spot bids.

        Raises:
            ValueError: If no API key is configured.
            httpx.TimeoutException: If the request times out.
            httpx.HTTPStatusError: If the server returns an error status.
            httpx.RequestError: If a network-level error occurs.
        """
        url = f"{self._base_url}{self._SPOT_BID_CURRENT_PATH}"
        logger.debug("GET %s", url)
        response = self._http.get(url, headers=self._auth_headers())
        response.raise_for_status()
        logger.debug("Response %s (%d bytes)", response.status_code, len(response.text))
        data: dict[str, list[dict[str, Any]]] = json.loads(
            response.text, parse_float=Decimal
        )

        return tuple(_parse_user_bid(item) for item in data["items"])

    def create_bid(
        self,
        upstream: Upstream,
        amount_sat: Sats,
        price: HashratePrice,
        speed_limit: Hashrate,
        cl_order_id: ClOrderId,
    ) -> CreateBidResult:
        """Create a new spot bid.

        Raises:
            ApiError: If the API returns a non-2xx response.
        """
        url = f"{self._base_url}{self._SPOT_BID_PATH}"
        body = {
            "dest_upstream": {
                "url": str(upstream.url),
                "identity": upstream.identity,
            },
            "amount_sat": amount_sat,
            "price_sat": self._price_to_api_sats(price),
            "speed_limit_ph": self._speed_to_api_value(speed_limit),
            "cl_order_id": cl_order_id,
        }
        logger.debug("POST %s %s", url, body)
        response = self._http.post(url, json=body, headers=self._auth_headers())
        logger.debug("Response %s (%d bytes)", response.status_code, len(response.text))
        if not response.is_success:
            self._raise_api_error(response)
        data: dict[str, str] = response.json()
        return CreateBidResult(id=BidId(data["id"]))

    def edit_bid(
        self,
        bid_id: BidId,
        new_price: HashratePrice,
        new_speed_limit: Hashrate,
    ) -> None:
        """Edit an existing spot bid's price and speed limit.

        Raises:
            ApiError: If the API returns a non-2xx response.
        """
        url = f"{self._base_url}{self._SPOT_BID_PATH}"
        body: dict[str, Any] = {
            "bid_id": bid_id,
            "new_price_sat": self._price_to_api_sats(new_price),
            "new_speed_limit_ph": {"value": self._speed_to_api_value(new_speed_limit)},
        }
        logger.debug("PUT %s %s", url, body)
        response = self._http.put(url, json=body, headers=self._auth_headers())
        logger.debug("Response %s (%d bytes)", response.status_code, len(response.text))
        if not response.is_success:
            self._raise_api_error(response)

    def get_market_settings(self) -> MarketSettings:
        """Fetch the current spot market settings.

        Returns:
            The market settings including bid cooldown periods.

        Raises:
            httpx.TimeoutException: If the request times out.
            httpx.HTTPStatusError: If the server returns an error status.
            httpx.RequestError: If a network-level error occurs.
        """
        url = f"{self._base_url}{self._SPOT_SETTINGS_PATH}"
        logger.debug("GET %s", url)
        response = self._http.get(url, headers=self._auth_headers())
        response.raise_for_status()
        logger.debug("Response %s (%d bytes)", response.status_code, len(response.text))
        data: dict[str, Any] = response.json()
        return MarketSettings(
            min_bid_price_decrease_period=timedelta(
                seconds=int(data["min_bid_price_decrease_period_s"])
            ),
            min_bid_speed_limit_decrease_period=timedelta(
                seconds=int(data["min_bid_speed_limit_decrease_period_s"])
            ),
            price_tick=PriceTick(sats=Sats(int(data["tick_size_sat"]))),
        )

    def get_account_balance(self) -> AccountBalance:
        """Fetch the authenticated account's balance.

        Expects the response to contain exactly one account.

        Raises:
            ValueError: If no API key is configured, or if the response
                does not contain exactly one account.
            httpx.TimeoutException: If the request times out.
            httpx.HTTPStatusError: If the server returns an error status.
            httpx.RequestError: If a network-level error occurs.
        """
        url = f"{self._base_url}{self._ACCOUNT_BALANCE_PATH}"
        logger.debug("GET %s", url)
        response = self._http.get(url, headers=self._auth_headers())
        response.raise_for_status()
        logger.debug("Response %s (%d bytes)", response.status_code, len(response.text))
        data: dict[str, Any] = response.json()
        accounts = data["accounts"]
        if len(accounts) != 1:
            raise ValueError(
                f"expected exactly one account in balance response, got {len(accounts)}"
            )
        account = accounts[0]
        return AccountBalance(
            available_sat=Sats(int(account["available_balance_sat"])),
            blocked_sat=Sats(int(account["blocked_balance_sat"])),
            total_sat=Sats(int(account["total_balance_sat"])),
        )

    def cancel_bid(self, order_id: BidId) -> None:
        """Cancel an existing spot bid.

        Raises:
            ApiError: If the API returns a non-2xx response.
        """
        url = f"{self._base_url}{self._SPOT_BID_PATH}"
        body = {"order_id": order_id}
        logger.debug("DELETE %s %s", url, body)
        response = self._http.request(
            "DELETE", url, json=body, headers=self._auth_headers()
        )
        logger.debug("Response %s (%d bytes)", response.status_code, len(response.text))
        if not response.is_success:
            self._raise_api_error(response)

    def get_bid_history(self, bid_id: BidId) -> BidHistory:
        """Fetch a bid's full history.

        Returns:
            A BidHistory with the bid's entries normalised newest-first.

        Raises:
            ApiError: If the API returns a non-2xx response (e.g. 404 for
                an unknown bid, 403 for a bid owned by another user).
        """
        url = f"{self._base_url}{self._SPOT_BID_DETAIL_PATH}/{bid_id}"
        logger.debug("GET %s", url)
        response = self._http.get(url, headers=self._auth_headers())
        logger.debug("Response %s (%d bytes)", response.status_code, len(response.text))
        if not response.is_success:
            self._raise_api_error(response)
        data: dict[str, Any] = json.loads(response.text, parse_float=Decimal)
        history = data.get("history") or []
        entries = tuple(
            BidHistoryEntry(
                timestamp=datetime.fromisoformat(item["timestamp"]),
                price=HashratePrice(
                    sats=Sats(int(item["price_sat"])),
                    per=Hashrate(Decimal(1), self._API_HASH_UNIT, self._API_TIME_UNIT),
                ),
                speed_limit_ph=Hashrate(
                    Decimal(item["speed_limit_ph"]),
                    self._API_SPEED_HASH_UNIT,
                    self._API_SPEED_TIME_UNIT,
                ),
            )
            for item in history
        )
        return BidHistory(entries=entries)
