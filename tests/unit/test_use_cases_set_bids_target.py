"""Tests for the set_bids_target use case orchestrator."""

from decimal import Decimal

import pytest

from hashbidder.client import BidItem, OrderBook
from hashbidder.config import TargetHashrateConfig
from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.ocean_client import AccountStats, HashrateWindow, OceanTimeWindow
from hashbidder.use_cases.set_bids_target import set_bids_target
from tests.conftest import UPSTREAM, FakeClient, FakeOceanSource

ADDRESS = BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
EH_DAY = Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY)


def _ph_s(value: str) -> Hashrate:
    return Hashrate(Decimal(value), HashUnit.PH, TimeUnit.SECOND)


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


def _orderbook(served_price_sat: int) -> OrderBook:
    return OrderBook(
        bids=(
            BidItem(
                price=HashratePrice(sats=Sats(served_price_sat), per=EH_DAY),
                amount_sat=Sats(100_000),
                hr_matched_ph=_ph_s("3"),
                speed_limit_ph=_ph_s("10"),
            ),
        ),
        asks=(),
    )


def _config(target_ph_s: str, max_bids_count: int = 3) -> TargetHashrateConfig:
    return TargetHashrateConfig(
        default_amount=Sats(100_000),
        upstream=UPSTREAM,
        target_hashrate=_ph_s(target_ph_s),
        max_bids_count=max_bids_count,
    )


class TestSetBidsTarget:
    """Tests for set_bids_target."""

    def test_happy_path_below_target_creates_bids(self) -> None:
        """Below target → plan creates bids at market price + 1."""
        client = FakeClient(orderbook=_orderbook(served_price_sat=800_000))
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(client, ocean, ADDRESS, _config("10"), dry_run=True)

        inputs = result.inputs
        assert inputs.ocean_24h == _ph_s("5")
        assert inputs.target == _ph_s("10")
        assert inputs.needed == _ph_s("15")
        assert inputs.price.sats == Sats(800_001)

        plan = result.set_bids_result.plan
        assert len(plan.creates) == 3
        for create in plan.creates:
            assert create.config.price.sats == Sats(800_001)
            assert create.config.speed_limit == _ph_s("5")

    def test_at_target_keeps_running(self) -> None:
        """Current == target → needed equals target, plan still creates bids."""
        client = FakeClient(orderbook=_orderbook(served_price_sat=500_000))
        ocean = FakeOceanSource(account_stats=_account_stats("10"))

        result = set_bids_target(client, ocean, ADDRESS, _config("10"), dry_run=True)

        assert result.inputs.needed == _ph_s("10")
        assert len(result.set_bids_result.plan.creates) == 3

    def test_far_above_target_creates_no_bids(self) -> None:
        """Current >= 2*target → needed clamps to zero and plan is empty."""
        client = FakeClient(orderbook=_orderbook(served_price_sat=500_000))
        ocean = FakeOceanSource(account_stats=_account_stats("25"))

        result = set_bids_target(client, ocean, ADDRESS, _config("10"), dry_run=True)

        assert result.inputs.needed == _ph_s("0")
        assert result.set_bids_result.plan.creates == ()

    def test_low_needed_single_bid(self) -> None:
        """Needed rounds up to a single 1 PH/s bid when below 1 PH/s."""
        # target=10, current=19.4 → needed=0.6 → single 1 PH/s bid
        client = FakeClient(orderbook=_orderbook(served_price_sat=500_000))
        ocean = FakeOceanSource(account_stats=_account_stats("19.4"))

        result = set_bids_target(client, ocean, ADDRESS, _config("10"), dry_run=True)

        assert result.inputs.needed == _ph_s("0.6")
        creates = result.set_bids_result.plan.creates
        assert len(creates) == 1
        assert creates[0].config.speed_limit == _ph_s("1")

    def test_missing_24h_window_raises(self) -> None:
        """Ocean stats without a 24h window raises ValueError."""
        stats = AccountStats(
            windows=(
                HashrateWindow(window=OceanTimeWindow.THREE_HOURS, hashrate=_ph_s("5")),
            ),
        )
        client = FakeClient(orderbook=_orderbook(served_price_sat=500_000))
        ocean = FakeOceanSource(account_stats=stats)

        with pytest.raises(ValueError, match="24h window"):
            set_bids_target(client, ocean, ADDRESS, _config("10"), dry_run=True)
