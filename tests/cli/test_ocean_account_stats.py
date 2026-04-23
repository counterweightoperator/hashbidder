"""CLI tests for the ocean-account-stats command."""

from decimal import Decimal

from click.testing import CliRunner

from hashbidder.cli.main import Clients, cli
from hashbidder.clients.ocean import (
    AccountStats,
    HashrateWindow,
    OceanError,
    OceanTimeWindow,
)
from hashbidder.domain.hashrate import Hashrate, HashUnit
from hashbidder.domain.time_unit import TimeUnit
from tests.conftest import FakeOceanSource

_ADDRESS = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"


def _make_stats(value: str = "100", unit: HashUnit = HashUnit.TH) -> AccountStats:
    return AccountStats(
        windows=tuple(
            HashrateWindow(
                window=tw,
                hashrate=Hashrate(Decimal(value), unit, TimeUnit.SECOND),
            )
            for tw in OceanTimeWindow
        )
    )


class TestOceanAccountStats:
    """Tests for the ocean-account-stats CLI command."""

    def test_happy_path(self) -> None:
        """Prints formatted stats for a valid address."""
        source = FakeOceanSource(account_stats=_make_stats())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ocean-account-stats"],
            obj=Clients(ocean=source),
            env={"OCEAN_ADDRESS": _ADDRESS},
        )

        assert result.exit_code == 0
        assert "Ocean stats for bc1qw50...f3t4" in result.output
        assert "24 hrs" in result.output

    def test_all_zeros(self) -> None:
        """All-zero hashrates show 'no stats' message."""
        source = FakeOceanSource(account_stats=_make_stats("0"))
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ocean-account-stats"],
            obj=Clients(ocean=source),
            env={"OCEAN_ADDRESS": _ADDRESS},
        )

        assert result.exit_code == 0
        assert "No stats found" in result.output

    def test_missing_env_var(self) -> None:
        """Missing OCEAN_ADDRESS results in error exit."""
        source = FakeOceanSource(account_stats=_make_stats())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ocean-account-stats"],
            obj=Clients(ocean=source),
            env={"OCEAN_ADDRESS": ""},
        )

        assert result.exit_code != 0
        assert "OCEAN_ADDRESS" in result.output

    def test_invalid_address(self) -> None:
        """Invalid OCEAN_ADDRESS results in error exit."""
        source = FakeOceanSource(account_stats=_make_stats())
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ocean-account-stats"],
            obj=Clients(ocean=source),
            env={"OCEAN_ADDRESS": "not-a-valid-address"},
        )

        assert result.exit_code != 0
        assert "invalid OCEAN_ADDRESS" in result.output

    def test_ocean_error(self) -> None:
        """OceanError results in non-zero exit code."""
        source = FakeOceanSource(
            account_stats=_make_stats(),
            error=OceanError(503, "service unavailable"),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["ocean-account-stats"],
            obj=Clients(ocean=source),
            env={"OCEAN_ADDRESS": _ADDRESS},
        )

        assert result.exit_code != 0
        assert "service unavailable" in result.output
