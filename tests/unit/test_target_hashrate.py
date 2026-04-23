"""Tests for target-hashrate pure computations."""

from decimal import Decimal

import pytest

from hashbidder.clients.braiins import BidItem, OrderBook
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.price_tick import PriceTick
from hashbidder.domain.sats import Sats
from hashbidder.domain.target_hashrate import compute_needed_hashrate
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.services.target_hashrate import find_market_price

EH_DAY = Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY)
PH_DAY = Hashrate(Decimal(1), HashUnit.PH, TimeUnit.DAY)


def _ph_s(value: str) -> Hashrate:
    return Hashrate(Decimal(value), HashUnit.PH, TimeUnit.SECOND)


def _bid_item(price_sat: int, hr_matched: str, speed_limit: str = "10") -> BidItem:
    return BidItem(
        price=HashratePrice(sats=Sats(price_sat), per=EH_DAY),
        amount_sat=Sats(100_000),
        hr_matched_ph=Hashrate(Decimal(hr_matched), HashUnit.PH, TimeUnit.SECOND),
        speed_limit_ph=Hashrate(Decimal(speed_limit), HashUnit.PH, TimeUnit.SECOND),
    )


class TestComputeNeededHashrate:
    """Tests for compute_needed_hashrate."""

    def test_below_target(self) -> None:
        """Current 5, target 10 → needed 45 (= 8*10 - 7*5)."""
        result = compute_needed_hashrate(
            target=Hashrate(Decimal("10"), HashUnit.PH, TimeUnit.SECOND),
            current_24h=Hashrate(Decimal("5"), HashUnit.PH, TimeUnit.SECOND),
        )
        assert result == Hashrate(Decimal("45"), HashUnit.PH, TimeUnit.SECOND)

    def test_at_target_keeps_running(self) -> None:
        """Current equal to target → keep running at target (8*10 - 7*10 = 10)."""
        result = compute_needed_hashrate(
            target=Hashrate(Decimal("10"), HashUnit.PH, TimeUnit.SECOND),
            current_24h=Hashrate(Decimal("10"), HashUnit.PH, TimeUnit.SECOND),
        )
        assert result == Hashrate(Decimal("10"), HashUnit.PH, TimeUnit.SECOND)

    def test_above_target_clamps_to_zero(self) -> None:
        """Target 12, current 15 → 0 (8*12 - 7*15 < 0; can't go negative)."""
        result = compute_needed_hashrate(
            target=Hashrate(Decimal("12"), HashUnit.PH, TimeUnit.SECOND),
            current_24h=Hashrate(Decimal("15"), HashUnit.PH, TimeUnit.SECOND),
        )
        assert result == Hashrate(Decimal("0"), HashUnit.PH, TimeUnit.SECOND)

    def test_far_above_target_clamps_to_zero(self) -> None:
        """Current >= (8/7)*target → 0 (can't go negative)."""
        result = compute_needed_hashrate(
            target=Hashrate(Decimal("10"), HashUnit.PH, TimeUnit.SECOND),
            current_24h=Hashrate(Decimal("25"), HashUnit.PH, TimeUnit.SECOND),
        )
        assert result == Hashrate(Decimal("0"), HashUnit.PH, TimeUnit.SECOND)

    def test_result_in_ph_per_second(self) -> None:
        """Result is always denominated in PH/s regardless of input units."""
        target = Hashrate(Decimal("864"), HashUnit.PH, TimeUnit.DAY)  # = 0.01 PH/s
        current = Hashrate(Decimal("0"), HashUnit.PH, TimeUnit.SECOND)
        result = compute_needed_hashrate(target, current)
        assert result.hash_unit == HashUnit.PH
        assert result.time_unit == TimeUnit.SECOND


_TICK = PriceTick(sats=Sats(100))


class TestFindMarketPrice:
    """Tests for find_market_price."""

    def test_picks_lowest_served_plus_one_tick(self) -> None:
        """Among served bids, picks the lowest aligned price and adds one tick."""
        orderbook = OrderBook(
            bids=(
                _bid_item(price_sat=1000, hr_matched="0"),
                _bid_item(price_sat=500, hr_matched="0"),
                _bid_item(price_sat=800, hr_matched="3"),
                _bid_item(price_sat=700, hr_matched="2"),
                _bid_item(price_sat=900, hr_matched="1"),
            ),
            asks=(),
        )
        price = find_market_price(orderbook, _TICK)
        assert price.sats == Sats(800)
        assert price.per == EH_DAY

    def test_single_served_bid(self) -> None:
        """A single served bid → that price aligned down, plus one tick."""
        orderbook = OrderBook(
            bids=(_bid_item(price_sat=1234, hr_matched="0.5"),),
            asks=(),
        )
        price = find_market_price(orderbook, _TICK)
        # 1234 → align_down to 1200 → +100 tick = 1300
        assert price.sats == Sats(1300)

    def test_result_is_tick_aligned(self) -> None:
        """Result is always aligned to the supplied tick."""
        orderbook = OrderBook(
            bids=(_bid_item(price_sat=12345, hr_matched="1"),),
            asks=(),
        )
        tick = PriceTick(sats=Sats(1000))
        price = find_market_price(orderbook, tick)
        assert int(price.sats) % 1000 == 0

    def test_no_served_bids_raises(self) -> None:
        """Order book with no served bids raises ValueError."""
        orderbook = OrderBook(
            bids=(
                _bid_item(price_sat=500, hr_matched="0"),
                _bid_item(price_sat=800, hr_matched="0"),
            ),
            asks=(),
        )
        with pytest.raises(ValueError, match="no served bids"):
            find_market_price(orderbook, _TICK)

    def test_empty_orderbook_raises(self) -> None:
        """Empty bids tuple raises ValueError."""
        with pytest.raises(ValueError, match="no served bids"):
            find_market_price(OrderBook(bids=(), asks=()), _TICK)


DESIRED_PRICE = HashratePrice(sats=Sats(500), per=PH_DAY)
