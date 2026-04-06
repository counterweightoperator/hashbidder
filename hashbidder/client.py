"""Braiins Hashpower API client."""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import httpx

from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit

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


class HashpowerClient(Protocol):
    """Protocol for hashpower market clients."""

    def get_orderbook(self) -> OrderBook:
        """Fetch the current spot order book."""
        ...


class BraiinsClient:
    """HTTP client for the Braiins Hashpower API."""

    _SPOT_ORDERBOOK_PATH = "/spot/orderbook"

    def __init__(
        self,
        base_url: httpx.URL = API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client.

        Args:
            base_url: The base URL of the Braiins Hashpower API.
            timeout: Request timeout in seconds.
        """
        self._base_url = base_url
        self._timeout = timeout

    def get_orderbook(self) -> OrderBook:
        """Fetch the current spot order book.

        Returns:
            A structured snapshot of the order book with bids and asks.

        Raises:
            httpx.TimeoutException: If the request times out.
            httpx.HTTPStatusError: If the server returns an error status.
            httpx.RequestError: If a network-level error occurs.
        """
        response = httpx.get(
            f"{self._base_url}{self._SPOT_ORDERBOOK_PATH}", timeout=self._timeout
        )
        response.raise_for_status()
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
                        item["hr_matched_ph"], HashUnit.PH, TimeUnit.SECOND
                    ),
                    speed_limit_ph=Hashrate(
                        item["speed_limit_ph"], HashUnit.PH, TimeUnit.SECOND
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
                        item["hr_matched_ph"], HashUnit.PH, TimeUnit.SECOND
                    ),
                    hr_available_ph=Hashrate(
                        item["hr_available_ph"], HashUnit.PH, TimeUnit.SECOND
                    ),
                )
                for item in data["asks"]
            ),
        )
