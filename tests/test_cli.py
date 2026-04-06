"""CLI integration tests."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from hashbidder.client import AmountSat, AskItem, BidItem, OrderBook
from hashbidder.hashrate import Hashrate, HashratePrice, HashUnit, Sats, TimeUnit
from hashbidder.main import cli


@pytest.fixture
def runner() -> CliRunner:
    """Return a Click test runner."""
    return CliRunner()


@pytest.fixture
def mock_client() -> MagicMock:
    """Return a mock BraiinsClient."""
    return MagicMock()


def test_ping_prints_orderbook_summary(
    runner: CliRunner, mock_client: MagicMock
) -> None:
    """Ping prints the number of bids and asks from the order book."""
    price = HashratePrice(sats=Sats(100), per=Hashrate(1, HashUnit.EH, TimeUnit.DAY))
    mock_client.get_orderbook.return_value = OrderBook(
        bids=[
            BidItem(
                price=price,
                amount_sat=AmountSat(50),
                hr_matched_ph=Hashrate(1.0, HashUnit.PH, TimeUnit.SECOND),
                speed_limit_ph=Hashrate(0, HashUnit.PH, TimeUnit.SECOND),
            )
        ]
        * 10,
        asks=[
            AskItem(
                price=price,
                hr_matched_ph=Hashrate(0.5, HashUnit.PH, TimeUnit.SECOND),
                hr_available_ph=Hashrate(2.0, HashUnit.PH, TimeUnit.SECOND),
            )
        ]
        * 4,
    )

    with patch("hashbidder.main.BraiinsClient", return_value=mock_client):
        result = runner.invoke(cli, ["ping"])

    assert result.exit_code == 0
    assert result.output == "OK — order book: 10 bids, 4 asks\n"
