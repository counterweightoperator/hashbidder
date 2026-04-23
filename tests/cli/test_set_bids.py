"""CLI tests for the set-bids command."""

from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import CliRunner

from hashbidder.cli.main import Clients, cli
from hashbidder.clients.braiins import AccountBalance, BidItem, OrderBook
from hashbidder.clients.ocean import AccountStats, HashrateWindow, OceanTimeWindow
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from tests.conftest import (
    EH_DAY,
    OTHER_UPSTREAM,
    UPSTREAM,
    FakeClient,
    FakeOceanSource,
    make_user_bid,
)

_OCEAN_ADDRESS = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"

TARGET_TOML = """\
mode = "target-hashrate"
default_amount_sat = 100000
target_hashrate_ph_s = 10

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"
"""


def _ph_s(value: str) -> Hashrate:
    return Hashrate(Decimal(value), HashUnit.PH, TimeUnit.SECOND)


def _target_orderbook() -> OrderBook:
    return OrderBook(
        bids=(
            BidItem(
                price=HashratePrice(sats=Sats(800_000), per=EH_DAY),
                amount_sat=Sats(100_000),
                hr_matched_ph=_ph_s("3"),
                speed_limit_ph=_ph_s("10"),
            ),
        ),
        asks=(),
    )


def _account_stats(day_ph_s: str) -> AccountStats:
    return AccountStats(
        windows=(
            HashrateWindow(window=OceanTimeWindow.DAY, hashrate=_ph_s(day_ph_s)),
            HashrateWindow(window=OceanTimeWindow.THREE_HOURS, hashrate=_ph_s("0")),
            HashrateWindow(window=OceanTimeWindow.TEN_MINUTES, hashrate=_ph_s("0")),
            HashrateWindow(window=OceanTimeWindow.FIVE_MINUTES, hashrate=_ph_s("0")),
            HashrateWindow(window=OceanTimeWindow.SIXTY_SECONDS, hashrate=_ph_s("0")),
        ),
    )


TOML_HEADER = """\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"
"""


def test_dry_run_creates_all(tmp_path: Path) -> None:
    """set-bids with no existing bids creates all config entries."""
    config_file = tmp_path / "bids.toml"
    config_file.write_text(
        TOML_HEADER
        + """\
[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0

[[bids]]
price_sat_per_ph_day = 300
speed_limit_ph_s = 10.0
"""
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["set-bids", "--bid-config", str(config_file), "--dry-run"],
        obj=Clients(braiins=FakeClient()),
    )

    assert result.exit_code == 0
    assert result.output.count("CREATE:") == 2
    assert "500 sat/PH/Day" in result.output
    assert "300 sat/PH/Day" in result.output
    assert "=== Expected Final State ===" in result.output
    assert result.output.count("(NEW)") == 2


def test_dry_run_end_to_end(tmp_path: Path) -> None:
    """End-to-end dry run with existing bids: edit, cancel, create."""
    existing_bids = (
        # Matches config entry at 500 sat/PH/Day, 5.0 PH/s — but price
        # is 400 → edit.
        make_user_bid("B1", 400, "5.0", amount=200_000, upstream=UPSTREAM),
        # No matching config entry (only 1 config bid) → cancel.
        make_user_bid("B2", 600, "3.0", remaining=50_000, upstream=UPSTREAM),
    )

    config_file = tmp_path / "bids.toml"
    config_file.write_text(
        TOML_HEADER
        + """\
[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0
"""
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["set-bids", "--bid-config", str(config_file), "--dry-run"],
        obj=Clients(braiins=FakeClient(current_bids=existing_bids)),
    )

    assert result.exit_code == 0
    assert "EDIT B1:" in result.output
    assert "400 → 500 sat/PH/Day" in result.output
    assert "CANCEL B2:" in result.output
    assert "=== Expected Final State ===" in result.output
    assert "(EDITED" in result.output


def test_invalid_config(tmp_path: Path) -> None:
    """set-bids command reports error for invalid config."""
    config_file = tmp_path / "bad.toml"
    config_file.write_text("not valid toml [[[")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["set-bids", "--bid-config", str(config_file), "--dry-run"],
        obj=Clients(braiins=FakeClient()),
    )

    assert result.exit_code != 0
    assert "Invalid TOML" in result.output


def test_without_dry_run_no_changes(tmp_path: Path) -> None:
    """set-bids without --dry-run prints no changes when config is empty."""
    config_file = tmp_path / "bids.toml"
    config_file.write_text(TOML_HEADER)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["set-bids", "--bid-config", str(config_file)],
        obj=Clients(braiins=FakeClient()),
    )

    assert result.exit_code == 0
    assert "No changes needed." in result.output


def test_insufficient_balance_aborts_with_nonzero_exit(tmp_path: Path) -> None:
    """An insufficient balance aborts execution and exits non-zero."""
    config_file = tmp_path / "bids.toml"
    config_file.write_text(
        TOML_HEADER
        + """\
[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0
"""
    )

    poor_balance = AccountBalance(
        available_sat=Sats(10),
        blocked_sat=Sats(0),
        total_sat=Sats(10),
    )
    client = FakeClient(account_balance=poor_balance)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["set-bids", "--bid-config", str(config_file)], obj=Clients(braiins=client)
    )

    assert result.exit_code == 1
    assert "INSUFFICIENT" in result.output
    assert "Execution aborted" in result.output
    # No mutations were issued.
    mutations = [
        c for c in client.calls if c[0] in ("create_bid", "edit_bid", "cancel_bid")
    ]
    assert mutations == []


def test_execute_happy_path(tmp_path: Path) -> None:
    """Execute with edit, cancel, and create — all succeed."""
    existing_bids = (
        # Matches config at 500/5.0 but price is 400 → edit.
        make_user_bid("B1", 400, "5.0", amount=200_000, upstream=UPSTREAM),
        # No matching config entry (only 1 config bid) → cancel.
        make_user_bid("B2", 600, "3.0", remaining=50_000, upstream=UPSTREAM),
    )

    config_file = tmp_path / "bids.toml"
    config_file.write_text(
        TOML_HEADER
        + """\
[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0
"""
    )

    client = FakeClient(current_bids=existing_bids)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["set-bids", "--bid-config", str(config_file)], obj=Clients(braiins=client)
    )

    assert result.exit_code == 0
    assert "=== Executing Changes ===" in result.output
    assert "CANCEL B2... OK" in result.output
    assert "EDIT B1... OK" in result.output
    assert "=== Results ===" in result.output
    assert "2 succeeded, 0 failed" in result.output
    assert "=== Current Bids ===" in result.output
    # Final state should have only the edited bid.
    final_bids = client.get_current_bids()
    assert len(final_bids) == 1


def test_execute_creates_only(tmp_path: Path) -> None:
    """Execute with no existing bids — only creates."""
    config_file = tmp_path / "bids.toml"
    config_file.write_text(
        TOML_HEADER
        + """\
[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0

[[bids]]
price_sat_per_ph_day = 300
speed_limit_ph_s = 10.0
"""
    )

    client = FakeClient()
    runner = CliRunner()
    result = runner.invoke(
        cli, ["set-bids", "--bid-config", str(config_file)], obj=Clients(braiins=client)
    )

    assert result.exit_code == 0
    assert result.output.count("OK") >= 2
    assert "2 succeeded, 0 failed" in result.output
    assert "=== Current Bids ===" in result.output
    assert len(client.get_current_bids()) == 2


def test_execute_no_changes(tmp_path: Path) -> None:
    """Execute when config matches existing bids exactly."""
    existing_bids = (make_user_bid("B1", 500, "5.0", upstream=UPSTREAM),)

    config_file = tmp_path / "bids.toml"
    config_file.write_text(
        TOML_HEADER
        + """\
[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0
"""
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["set-bids", "--bid-config", str(config_file)],
        obj=Clients(braiins=FakeClient(current_bids=existing_bids)),
    )

    assert result.exit_code == 0
    assert "No changes needed." in result.output
    assert "=== Executing Changes ===" not in result.output


def test_execute_upstream_mismatch(tmp_path: Path) -> None:
    """Upstream mismatch produces cancel + create pair."""
    existing_bids = (make_user_bid("B1", 500, "5.0", upstream=OTHER_UPSTREAM),)

    config_file = tmp_path / "bids.toml"
    config_file.write_text(
        TOML_HEADER
        + """\
[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0
"""
    )

    client = FakeClient(current_bids=existing_bids)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["set-bids", "--bid-config", str(config_file)], obj=Clients(braiins=client)
    )

    assert result.exit_code == 0
    assert "CANCEL B1... OK" in result.output
    assert "CREATE 500 sat/PH/Day 5.0 PH/s... OK" in result.output
    assert "2 succeeded, 0 failed" in result.output
    # Old bid canceled, new one created.
    final_bids = client.get_current_bids()
    assert len(final_bids) == 1
    assert final_bids[0].id != "B1"


class TestTargetHashrateMode:
    """CLI tests for target-hashrate mode dispatch."""

    def test_dry_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Target mode dry-run prints inputs and plan without executing."""
        config_file = tmp_path / "bids.toml"
        config_file.write_text(TARGET_TOML)
        monkeypatch.setenv("OCEAN_ADDRESS", _OCEAN_ADDRESS)

        client = FakeClient(orderbook=_target_orderbook())
        ocean = FakeOceanSource(account_stats=_account_stats("5"))
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["set-bids", "--bid-config", str(config_file), "--dry-run"],
            obj=Clients(braiins=client, ocean=ocean),
        )

        assert result.exit_code == 0, result.output
        assert "=== Target Hashrate Inputs ===" in result.output
        assert "Ocean 24h:" in result.output
        assert "Needed:" in result.output
        assert "801 sat/PH/Day" in result.output
        assert result.output.count("CREATE:") == 1
        # Dry run did not execute anything.
        assert client.calls == []

    def test_execute(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Target mode without --dry-run creates bids on the fake client."""
        config_file = tmp_path / "bids.toml"
        config_file.write_text(TARGET_TOML)
        monkeypatch.setenv("OCEAN_ADDRESS", _OCEAN_ADDRESS)

        client = FakeClient(orderbook=_target_orderbook())
        ocean = FakeOceanSource(account_stats=_account_stats("5"))
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["set-bids", "--bid-config", str(config_file)],
            obj=Clients(braiins=client, ocean=ocean),
        )

        assert result.exit_code == 0, result.output
        assert "=== Target Hashrate Inputs ===" in result.output
        assert "1 succeeded, 0 failed" in result.output
        assert len(client.get_current_bids()) == 1

    def test_verbose_dry_run_prints_reasoning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`-v set-bids --dry-run` prints reasoning + cooldown sections."""
        config_file = tmp_path / "bids.toml"
        config_file.write_text(TARGET_TOML)
        monkeypatch.setenv("OCEAN_ADDRESS", _OCEAN_ADDRESS)

        client = FakeClient(orderbook=_target_orderbook())
        ocean = FakeOceanSource(account_stats=_account_stats("5"))
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["-v", "set-bids", "--bid-config", str(config_file), "--dry-run"],
            obj=Clients(braiins=client, ocean=ocean),
        )

        assert result.exit_code == 0, result.output
        assert "=== Target Hashrate Inputs ===" in result.output
        assert "=== Reasoning ===" in result.output
        assert "Price scan:" in result.output
        assert "Needed math:" in result.output
        assert "=== Cooldown Status ===" in result.output
        assert "(no existing bids)" in result.output

    def test_missing_ocean_address(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Target mode without OCEAN_ADDRESS errors out."""
        config_file = tmp_path / "bids.toml"
        config_file.write_text(TARGET_TOML)
        monkeypatch.delenv("OCEAN_ADDRESS", raising=False)

        client = FakeClient(orderbook=_target_orderbook())
        ocean = FakeOceanSource(account_stats=_account_stats("5"))
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["set-bids", "--bid-config", str(config_file), "--dry-run"],
            obj=Clients(braiins=client, ocean=ocean),
            env={"OCEAN_ADDRESS": ""},
        )

        assert result.exit_code != 0
        assert "OCEAN_ADDRESS" in result.output
