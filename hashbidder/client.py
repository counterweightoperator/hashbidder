"""Braiins Hashpower API client."""

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, NewType, Protocol
from urllib.parse import unquote

import httpx

from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.progress import Progress
from hashbidder.domain.sats import Sats
from hashbidder.domain.stratum_url import StratumUrl
from hashbidder.domain.time_unit import TimeUnit

logger = logging.getLogger(__name__)

API_BASE = httpx.URL("https://hashpower.braiins.com/v1")
DEFAULT_TIMEOUT = 10.0

BidId = NewType("BidId", str)
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
class Upstream:
    """Upstream pool specification for a bid."""

    url: StratumUrl
    identity: str


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
    progress: Progress
    amount_remaining_sat: Sats
    upstream: Upstream | None = None


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


class BraiinsClient:
    """HTTP client for the Braiins Hashpower API."""

    _SPOT_ORDERBOOK_PATH = "/spot/orderbook"
    _SPOT_BID_CURRENT_PATH = "/spot/bid/current"
    _SPOT_BID_PATH = "/spot/bid"

    # API wire units.
    _API_HASH_UNIT = HashUnit.EH
    _API_TIME_UNIT = TimeUnit.DAY
    _API_SPEED_HASH_UNIT = HashUnit.PH
    _API_SPEED_TIME_UNIT = TimeUnit.SECOND

    def __init__(
        self,
        base_url: httpx.URL = API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
        api_key: str | None = None,
    ) -> None:
        """Initialize the client.

        Args:
            base_url: The base URL of the Braiins Hashpower API.
            timeout: Request timeout in seconds.
            api_key: API token for authenticated endpoints.
        """
        self._base_url = base_url
        self._timeout = timeout
        self._api_key = api_key

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
        response = httpx.get(url, timeout=self._timeout)
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
        response = httpx.get(url, headers=self._auth_headers(), timeout=self._timeout)
        response.raise_for_status()
        logger.debug("Response %s (%d bytes)", response.status_code, len(response.text))
        data: dict[str, list[dict[str, Any]]] = json.loads(
            response.text, parse_float=Decimal
        )
        return tuple(
            UserBid(
                id=BidId(item["bid"]["id"]),
                price=HashratePrice(
                    sats=Sats(int(item["bid"]["price_sat"])),
                    per=Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY),
                ),
                speed_limit_ph=Hashrate(
                    Decimal(item["bid"]["speed_limit_ph"]), HashUnit.PH, TimeUnit.SECOND
                ),
                amount_sat=Sats(int(item["bid"]["amount_sat"])),
                status=BidStatus(item["bid"]["status"]),
                progress=Progress.from_percentage(
                    Decimal(item["state_estimate"]["progress_pct"])
                ),
                amount_remaining_sat=Sats(
                    int(item["state_estimate"]["amount_remaining_sat"])
                ),
                upstream=Upstream(
                    url=StratumUrl(item["bid"]["dest_upstream"]["url"]),
                    identity=item["bid"]["dest_upstream"]["identity"],
                )
                if "dest_upstream" in item["bid"]
                else None,
            )
            for item in data["items"]
        )

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
        response = httpx.post(
            url, json=body, headers=self._auth_headers(), timeout=self._timeout
        )
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
        response = httpx.put(
            url, json=body, headers=self._auth_headers(), timeout=self._timeout
        )
        if not response.is_success:
            self._raise_api_error(response)

    def cancel_bid(self, order_id: BidId) -> None:
        """Cancel an existing spot bid.

        Raises:
            ApiError: If the API returns a non-2xx response.
        """
        url = f"{self._base_url}{self._SPOT_BID_PATH}"
        logger.debug("DELETE %s order_id=%s", url, order_id)
        response = httpx.delete(
            url,
            params={"order_id": order_id},
            headers=self._auth_headers(),
            timeout=self._timeout,
        )
        if not response.is_success:
            self._raise_api_error(response)
