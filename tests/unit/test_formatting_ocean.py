"""Tests for Ocean stats formatting."""

from decimal import Decimal

from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashUnit
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.formatting import format_ocean_stats
from hashbidder.ocean_client import AccountStats, HashrateWindow, OceanTimeWindow


def _window(tw: OceanTimeWindow, value: str, unit: HashUnit) -> HashrateWindow:
    return HashrateWindow(
        window=tw,
        hashrate=Hashrate(Decimal(value), unit, TimeUnit.SECOND),
    )


class TestFormatOceanStats:
    """Tests for format_ocean_stats."""

    def test_normal_output(self) -> None:
        """Formats multiple windows with auto-selected units."""
        stats = AccountStats(
            windows=(
                _window(OceanTimeWindow.DAY, "1885800", HashUnit.GH),
                _window(OceanTimeWindow.THREE_HOURS, "1850000", HashUnit.GH),
                _window(OceanTimeWindow.TEN_MINUTES, "3220", HashUnit.GH),
                _window(OceanTimeWindow.FIVE_MINUTES, "3020", HashUnit.GH),
                _window(OceanTimeWindow.SIXTY_SECONDS, "3000", HashUnit.GH),
            )
        )

        output = format_ocean_stats(
            stats,
            BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"),
        )

        assert "Ocean stats for bc1qw50...f3t4" in output
        assert "24 hrs" in output
        assert "60 sec" in output
        # 1885800 GH/s -> 1.89 PH/s (display_unit picks PH)
        assert "PH/s" in output

    def test_all_zeros(self) -> None:
        """All-zero hashrates produce a 'no stats' message."""
        stats = AccountStats(
            windows=tuple(_window(tw, "0", HashUnit.TH) for tw in OceanTimeWindow)
        )

        output = format_ocean_stats(
            stats,
            BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"),
        )

        assert "No stats found" in output
        assert "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4" in output

    def test_legacy_address_truncated(self) -> None:
        """Legacy P2PKH address is truncated in the header."""
        stats = AccountStats(
            windows=tuple(_window(tw, "100", HashUnit.TH) for tw in OceanTimeWindow)
        )

        output = format_ocean_stats(
            stats, BtcAddress("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        )

        assert "1A1zP1e...vfNa" in output
