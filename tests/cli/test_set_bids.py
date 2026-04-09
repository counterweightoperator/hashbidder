"""CLI tests for the set-bids command."""

from pathlib import Path

from click.testing import CliRunner

from hashbidder.main import cli
from tests.conftest import OTHER_UPSTREAM, UPSTREAM, FakeClient, make_user_bid

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
        obj=FakeClient(),
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
        obj=FakeClient(current_bids=existing_bids),
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
        obj=FakeClient(),
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
        obj=FakeClient(),
    )

    assert result.exit_code == 0
    assert "No changes needed." in result.output


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
        cli, ["set-bids", "--bid-config", str(config_file)], obj=client
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
        cli, ["set-bids", "--bid-config", str(config_file)], obj=client
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
        obj=FakeClient(current_bids=existing_bids),
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
        cli, ["set-bids", "--bid-config", str(config_file)], obj=client
    )

    assert result.exit_code == 0
    assert "CANCEL B1... OK" in result.output
    assert "CREATE 500 sat/PH/Day 5.0 PH/s... OK" in result.output
    assert "2 succeeded, 0 failed" in result.output
    # Old bid canceled, new one created.
    final_bids = client.get_current_bids()
    assert len(final_bids) == 1
    assert final_bids[0].id != "B1"
