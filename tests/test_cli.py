"""CLI integration tests."""

from decimal import Decimal
from pathlib import Path

from click.testing import CliRunner

from hashbidder.client import AskItem, BidItem, OrderBook
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.main import cli
from tests.conftest import UPSTREAM, FakeClient, make_user_bid


def test_ping_prints_orderbook_summary() -> None:
    """Ping prints the number of bids and asks from the order book."""
    price = HashratePrice(
        sats=Sats(100), per=Hashrate(Decimal("1"), HashUnit.EH, TimeUnit.DAY)
    )
    book = OrderBook(
        bids=(
            BidItem(
                price=price,
                amount_sat=Sats(50),
                hr_matched_ph=Hashrate(Decimal("1.0"), HashUnit.PH, TimeUnit.SECOND),
                speed_limit_ph=Hashrate(Decimal("0"), HashUnit.PH, TimeUnit.SECOND),
            ),
        )
        * 10,
        asks=(
            AskItem(
                price=price,
                hr_matched_ph=Hashrate(Decimal("0.5"), HashUnit.PH, TimeUnit.SECOND),
                hr_available_ph=Hashrate(Decimal("2.0"), HashUnit.PH, TimeUnit.SECOND),
            ),
        )
        * 4,
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["ping"], obj=FakeClient(orderbook=book))

    assert result.exit_code == 0
    assert result.output == "OK — order book: 10 bids, 4 asks\n"


def test_bids_prints_active_bids() -> None:
    """Bids command prints each active bid with key details."""
    bid = make_user_bid("B123456789", 500, "5.0")

    runner = CliRunner()
    result = runner.invoke(cli, ["bids"], obj=FakeClient(current_bids=(bid,)))

    assert result.exit_code == 0
    assert "B123456789" in result.output
    assert "ACTIVE" in result.output


def test_bids_no_active_bids() -> None:
    """Bids command prints a message when there are no active bids."""
    runner = CliRunner()
    result = runner.invoke(cli, ["bids"], obj=FakeClient())

    assert result.exit_code == 0
    assert result.output == "No active bids.\n"


def test_set_bids_dry_run_creates_all(tmp_path: Path) -> None:
    """set-bids with no existing bids creates all config entries."""
    config_file = tmp_path / "bids.toml"
    config_file.write_text("""\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"

[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0

[[bids]]
price_sat_per_ph_day = 300
speed_limit_ph_s = 10.0
""")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["set-bids", "--bid-config", str(config_file), "--dry-run"],
        obj=FakeClient(),
    )

    assert result.exit_code == 0
    assert result.output.count("CREATE:") == 2
    assert "500 sat/PH/Day" in result.output
    assert "300 sat/PH/Day" in result.output
    assert "=== Expected Final State ===" in result.output
    assert result.output.count("(NEW)") == 2


def test_set_bids_dry_run_end_to_end(tmp_path: Path) -> None:
    """End-to-end dry run with existing bids: edit, cancel, create."""
    existing_bids = (
        # Matches config entry at 500 sat/PH/Day, 5.0 PH/s — but price
        # is 400 → edit.
        make_user_bid("B1", 400, "5.0", amount=200_000, upstream=UPSTREAM),
        # No matching config entry (only 1 config bid) → cancel.
        make_user_bid("B2", 600, "3.0", remaining=50_000, upstream=UPSTREAM),
    )

    config_file = tmp_path / "bids.toml"
    config_file.write_text("""\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"

[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0
""")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["set-bids", "--bid-config", str(config_file), "--dry-run"],
        obj=FakeClient(current_bids=existing_bids),
    )

    assert result.exit_code == 0
    assert "EDIT B1:" in result.output
    assert "400 \u2192 500 sat/PH/Day" in result.output
    assert "CANCEL B2:" in result.output
    assert "=== Expected Final State ===" in result.output
    assert "(EDITED" in result.output


def test_set_bids_invalid_config(tmp_path: Path) -> None:
    """set-bids command reports error for invalid config."""
    config_file = tmp_path / "bad.toml"
    config_file.write_text("not valid toml [[[")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["set-bids", "--bid-config", str(config_file), "--dry-run"],
        obj=FakeClient(),
    )

    assert result.exit_code != 0
    assert "Invalid TOML" in result.output


def test_set_bids_without_dry_run_no_changes(tmp_path: Path) -> None:
    """set-bids without --dry-run prints no changes when config is empty."""
    config_file = tmp_path / "bids.toml"
    config_file.write_text("""\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"
""")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["set-bids", "--bid-config", str(config_file)],
        obj=FakeClient(),
    )

    assert result.exit_code == 0
    assert "No changes needed." in result.output
