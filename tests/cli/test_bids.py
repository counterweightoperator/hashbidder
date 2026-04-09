"""CLI tests for the bids command."""

from click.testing import CliRunner

from hashbidder.main import cli
from tests.conftest import FakeClient, make_user_bid


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
