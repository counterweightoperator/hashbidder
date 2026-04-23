"""Ocean.xyz API client for account hashrate stats."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Protocol

import httpx

from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashUnit
from hashbidder.domain.time_unit import TimeUnit

DEFAULT_OCEAN_URL = httpx.URL("https://ocean.xyz")


class OceanTimeWindow(Enum):
    """Hashrate averaging windows returned by Ocean."""

    DAY = "24 hrs"
    THREE_HOURS = "3 hrs"
    TEN_MINUTES = "10 min"
    FIVE_MINUTES = "5 min"
    SIXTY_SECONDS = "60 sec"


_ROW_RE = re.compile(r'<tr\s+class="table-row">(.*?)</tr>', re.DOTALL)
_CELL_RE = re.compile(r'<td\s+class="table-cell"\s*>(.*?)</td>', re.DOTALL)


class OceanError(Exception):
    """An error returned by or when parsing the Ocean API."""

    def __init__(self, status_code: int, message: str) -> None:
        """Initialize with the HTTP status code and error message."""
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code}: {message}")


@dataclass(frozen=True)
class HashrateWindow:
    """A single hashrate measurement over a time window."""

    window: OceanTimeWindow
    hashrate: Hashrate


@dataclass(frozen=True)
class AccountStats:
    """Hashrate stats for an Ocean account across all time windows."""

    windows: tuple[HashrateWindow, ...]


class OceanSource(Protocol):
    """Protocol for Ocean data sources."""

    def get_account_stats(self, address: BtcAddress) -> AccountStats:
        """Fetch hashrate stats for the given address."""
        ...


def _parse_hashrate(text: str) -> Hashrate:
    """Parse a hashrate string like '1885.8 Th/s' into a Hashrate."""
    parts = text.strip().split()
    if len(parts) != 2:
        raise OceanError(200, f"unexpected hashrate format: {text!r}")
    value_str, unit_str = parts
    try:
        value = Decimal(value_str)
    except InvalidOperation as e:
        raise OceanError(200, f"invalid hashrate value: {value_str!r}") from e
    try:
        hash_unit = HashUnit.from_rate_str(unit_str)
    except ValueError as e:
        raise OceanError(200, f"unrecognized hashrate unit: {unit_str!r}") from e
    return Hashrate(value=value, hash_unit=hash_unit, time_unit=TimeUnit.SECOND)


def _parse_html(html: str) -> AccountStats:
    """Parse the Ocean hashrate rows HTML fragment into AccountStats."""
    rows = _ROW_RE.findall(html)
    if len(rows) != 5:
        raise OceanError(
            200,
            f"expected 5 rows, got {len(rows)}; response schema may have changed",
        )

    expected_windows = tuple(OceanTimeWindow)
    windows: list[HashrateWindow] = []
    for i, row_html in enumerate(rows):
        cells = [c.strip() for c in _CELL_RE.findall(row_html)]
        if len(cells) != 3:
            raise OceanError(
                200,
                f"row {i}: expected 3 cells, got {len(cells)}",
            )
        label = cells[0]
        expected = expected_windows[i]
        if label != expected.value:
            raise OceanError(
                200,
                f"row {i}: expected period {expected.value!r}, got {label!r}",
            )
        hashrate = _parse_hashrate(cells[1])
        windows.append(HashrateWindow(window=expected, hashrate=hashrate))

    return AccountStats(windows=tuple(windows))


class OceanClient:
    """HTTP client for Ocean.xyz hashrate stats."""

    _HASHRATE_ROWS_PATH = "/template/workers/hashrates/rows"

    def __init__(self, base_url: httpx.URL, http_client: httpx.Client) -> None:
        """Initialize the client.

        Args:
            base_url: The base URL of the Ocean.xyz instance.
            http_client: The httpx.Client to use for requests.
        """
        self._base_url = base_url
        self._http = http_client

    def get_account_stats(self, address: BtcAddress) -> AccountStats:
        """Fetch hashrate stats for the given address.

        Raises:
            OceanError: On HTTP errors or unexpected response schema.
        """
        url = f"{self._base_url}{self._HASHRATE_ROWS_PATH}"
        resp = self._http.get(url, params={"user": address.value})
        if not resp.is_success:
            raise OceanError(
                resp.status_code,
                resp.text or resp.reason_phrase or "Unknown error",
            )
        return _parse_html(resp.text)
