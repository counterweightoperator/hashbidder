"""Tests for the get_ocean_account_stats use case."""

from decimal import Decimal

import pytest

from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashUnit
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.ocean_client import (
    AccountStats,
    HashrateWindow,
    OceanError,
    OceanTimeWindow,
)
from hashbidder.use_cases import get_ocean_account_stats
from tests.conftest import FakeOceanSource


def _make_stats() -> AccountStats:
    """Build a simple AccountStats for testing."""
    windows = tuple(
        HashrateWindow(
            window=tw,
            hashrate=Hashrate(Decimal("100"), HashUnit.TH, TimeUnit.SECOND),
        )
        for tw in OceanTimeWindow
    )
    return AccountStats(windows=windows)


class TestGetOceanAccountStats:
    """Tests for the get_ocean_account_stats use case."""

    def test_happy_path(self) -> None:
        """Returns account stats from source."""
        stats = _make_stats()
        source = FakeOceanSource(account_stats=stats)

        result = get_ocean_account_stats(
            source, BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
        )

        assert result == stats

    def test_error_propagates(self) -> None:
        """OceanError from source propagates to caller."""
        source = FakeOceanSource(
            account_stats=_make_stats(),
            error=OceanError(503, "service unavailable"),
        )

        with pytest.raises(OceanError) as exc_info:
            get_ocean_account_stats(
                source, BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
            )
        assert exc_info.value.status_code == 503
