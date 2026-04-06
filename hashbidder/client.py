"""Braiins Hashpower API client."""

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

import httpx

from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.progress import Progress
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit

logger = logging.getLogger(__name__)

API_BASE = httpx.URL("https://hashpower.braiins.com/v1")
DEFAULT_TIMEOUT = 10.0


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

    id: str
    price: HashratePrice
    speed_limit_ph: Hashrate
    amount_sat: Sats
    status: BidStatus
    progress: Progress
    amount_remaining_sat: Sats


class HashpowerClient(Protocol):
    """Protocol for hashpower market clients."""

    def get_orderbook(self) -> OrderBook:
        """Fetch the current spot order book."""
        ...

    def get_current_bids(self) -> tuple[UserBid, ...]:
        """Fetch the authenticated user's active bids."""
        ...


class BraiinsClient:
    """HTTP client for the Braiins Hashpower API."""

    _SPOT_ORDERBOOK_PATH = "/spot/orderbook"
    _SPOT_BID_CURRENT_PATH = "/spot/bid/current"

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
                id=item["bid"]["id"],
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
            )
            for item in data["items"]
        )
