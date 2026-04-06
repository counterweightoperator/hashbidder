"""Braiins Hashpower API client."""

import httpx

API_BASE = "https://hashpower.braiins.com/v1"


class BraiinsClient:
    """HTTP client for the Braiins Hashpower API."""

    def __init__(self, base_url: str = API_BASE) -> None:
        """Initialize the client.

        Args:
            base_url: The base URL of the Braiins Hashpower API.
        """
        self._base_url = base_url

    def get_orderbook(self) -> dict[str, list[dict[str, object]]]:
        """Fetch the current spot order book.

        Returns:
            The order book payload with bids and asks.

        Raises:
            httpx.TimeoutException: If the request times out.
            httpx.HTTPStatusError: If the server returns an error status.
            httpx.RequestError: If a network-level error occurs.
        """
        response = httpx.get(f"{self._base_url}/spot/orderbook", timeout=10)
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]
