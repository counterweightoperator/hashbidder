"""CLI tests for the ping command."""

from decimal import Decimal

from click.testing import CliRunner

from hashbidder.client import AskItem, BidItem, OrderBook
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.main import cli
from tests.conftest import FakeClient


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
