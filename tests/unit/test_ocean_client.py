"""Tests for OceanClient HTTP parsing and error handling."""

from decimal import Decimal

import httpx
import pytest

from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import HashUnit
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.ocean_client import OceanClient, OceanError, OceanTimeWindow

_VALID_HTML = """\
<tr class="table-row">
  <td class="table-cell">24 hrs</td>
  <td class="table-cell">1885.8 Th/s</td>
  <td class="table-cell">123 shares</td>
</tr>
<tr class="table-row">
  <td class="table-cell">3 hrs</td>
  <td class="table-cell">1850.0 Th/s</td>
  <td class="table-cell">45 shares</td>
</tr>
<tr class="table-row">
  <td class="table-cell">10 min</td>
  <td class="table-cell">3.22 Th/s</td>
  <td class="table-cell">5 shares</td>
</tr>
<tr class="table-row">
  <td class="table-cell">5 min</td>
  <td class="table-cell">3.02 Th/s</td>
  <td class="table-cell">3 shares</td>
</tr>
<tr class="table-row">
  <td class="table-cell">60 sec</td>
  <td class="table-cell">3.00 Th/s</td>
  <td class="table-cell">1 shares</td>
</tr>
"""


def _make_client(handler: httpx.MockTransport) -> OceanClient:
    return OceanClient(http_client=httpx.Client(transport=handler))


class TestGetAccountStats:
    """Tests for OceanClient.get_account_stats."""

    def test_happy_path(self) -> None:
        """Valid HTML fragment is parsed into correct AccountStats."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert "user=" in str(request.url)
            return httpx.Response(200, text=_VALID_HTML)

        client = _make_client(httpx.MockTransport(handler))
        stats = client.get_account_stats(
            BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
        )

        assert len(stats.windows) == 5
        assert stats.windows[0].window == OceanTimeWindow.DAY
        assert stats.windows[0].hashrate.value == Decimal("1885.8")
        assert stats.windows[0].hashrate.hash_unit == HashUnit.TH
        assert stats.windows[0].hashrate.time_unit == TimeUnit.SECOND
        assert stats.windows[4].window == OceanTimeWindow.SIXTY_SECONDS
        assert stats.windows[4].hashrate.value == Decimal("3.00")

    def test_wrong_number_of_rows(self) -> None:
        """Fewer than 5 rows raises OceanError."""
        html = (
            '<tr class="table-row">'
            '<td class="table-cell">24 hrs</td>'
            '<td class="table-cell">0.00 Th/s</td>'
            '<td class="table-cell">0</td>'
            "</tr>"
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(OceanError, match="expected 5 rows"):
            client.get_account_stats(
                BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
            )

    def test_unexpected_period_label(self) -> None:
        """Wrong period label raises OceanError."""
        html = _VALID_HTML.replace("24 hrs", "48 hrs")

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(OceanError, match="expected period"):
            client.get_account_stats(
                BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
            )

    def test_unrecognized_unit(self) -> None:
        """Unknown hashrate unit raises OceanError."""
        html = _VALID_HTML.replace("1885.8 Th/s", "1885.8 Xh/s")

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=html)

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(OceanError, match="unrecognized hashrate unit"):
            client.get_account_stats(
                BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
            )

    def test_http_error(self) -> None:
        """Non-2xx response raises OceanError with status code."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal server error")

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(OceanError) as exc_info:
            client.get_account_stats(
                BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
            )
        assert exc_info.value.status_code == 500
