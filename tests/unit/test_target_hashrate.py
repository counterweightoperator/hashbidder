"""Tests for target-hashrate pure computations."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hashbidder.client import BidItem, MarketSettings, OrderBook
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.target_hashrate import (
    BidWithCooldown,
    CooldownInfo,
    check_cooldowns,
    compute_needed_hashrate,
    distribute_bids,
    find_market_price,
    plan_with_cooldowns,
)
from tests.conftest import make_user_bid

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
        """Current 5, target 10 → needed 15 (= 2*10 - 5)."""
        result = compute_needed_hashrate(
            target=Hashrate(Decimal("10"), HashUnit.PH, TimeUnit.SECOND),
            current_24h=Hashrate(Decimal("5"), HashUnit.PH, TimeUnit.SECOND),
        )
        assert result == Hashrate(Decimal("15"), HashUnit.PH, TimeUnit.SECOND)

    def test_at_target_keeps_running(self) -> None:
        """Current equal to target → keep running at target (2*10 - 10 = 10)."""
        result = compute_needed_hashrate(
            target=Hashrate(Decimal("10"), HashUnit.PH, TimeUnit.SECOND),
            current_24h=Hashrate(Decimal("10"), HashUnit.PH, TimeUnit.SECOND),
        )
        assert result == Hashrate(Decimal("10"), HashUnit.PH, TimeUnit.SECOND)

    def test_modestly_above_target_undershoots(self) -> None:
        """Target 12, current 15 → 9 (= 2*12 - 15) to pull average down."""
        result = compute_needed_hashrate(
            target=Hashrate(Decimal("12"), HashUnit.PH, TimeUnit.SECOND),
            current_24h=Hashrate(Decimal("15"), HashUnit.PH, TimeUnit.SECOND),
        )
        assert result == Hashrate(Decimal("9"), HashUnit.PH, TimeUnit.SECOND)

    def test_far_above_target_clamps_to_zero(self) -> None:
        """Current >= 2*target → 0 (can't go negative)."""
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


class TestDistributeBids:
    """Tests for distribute_bids."""

    def test_five_phs_max_three(self) -> None:
        """5 PH/s into 3 bids: 3 equal shares quantized to 0.01 PH/s."""
        speeds = distribute_bids(
            Hashrate(Decimal("5"), HashUnit.PH, TimeUnit.SECOND), max_bids_count=3
        )
        assert len(speeds) == 3
        assert all(
            s >= Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.SECOND) for s in speeds
        )
        assert speeds == (
            Hashrate(Decimal("1.67"), HashUnit.PH, TimeUnit.SECOND),
            Hashrate(Decimal("1.67"), HashUnit.PH, TimeUnit.SECOND),
            Hashrate(Decimal("1.67"), HashUnit.PH, TimeUnit.SECOND),
        )

    def test_three_phs_max_seven_uses_three_bids(self) -> None:
        """3 PH/s with room for 7: 3 bids at 1 each (each >= 1 PH/s)."""
        speeds = distribute_bids(
            Hashrate(Decimal("3"), HashUnit.PH, TimeUnit.SECOND), max_bids_count=7
        )
        one = Hashrate(Decimal("1.00"), HashUnit.PH, TimeUnit.SECOND)
        assert speeds == (one, one, one)

    def test_two_point_five_phs_max_four_uses_two_bids(self) -> None:
        """2.5 PH/s with room for 4: 2 bids at 1.25 (3 would need 3 PH/s)."""
        speeds = distribute_bids(
            Hashrate(Decimal("2.5"), HashUnit.PH, TimeUnit.SECOND), max_bids_count=4
        )
        assert speeds == (
            Hashrate(Decimal("1.25"), HashUnit.PH, TimeUnit.SECOND),
            Hashrate(Decimal("1.25"), HashUnit.PH, TimeUnit.SECOND),
        )

    def test_below_half_returns_empty(self) -> None:
        """0.3 PH/s rounds to zero → cancel all."""
        assert (
            distribute_bids(
                Hashrate(Decimal("0.3"), HashUnit.PH, TimeUnit.SECOND), max_bids_count=3
            )
            == ()
        )

    def test_zero_returns_empty(self) -> None:
        """0 PH/s → empty (cancel all)."""
        assert (
            distribute_bids(
                Hashrate(Decimal("0"), HashUnit.PH, TimeUnit.SECOND), max_bids_count=3
            )
            == ()
        )

    def test_between_half_and_one_rounds_up_to_single_bid(self) -> None:
        """0.7 PH/s → single bid at 1 PH/s minimum."""
        speeds = distribute_bids(
            Hashrate(Decimal("0.7"), HashUnit.PH, TimeUnit.SECOND), max_bids_count=3
        )
        assert speeds == (Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.SECOND),)

    def test_exactly_one_phs_max_one(self) -> None:
        """1 PH/s with max_bids_count=1 → single bid at 1 PH/s."""
        speeds = distribute_bids(
            Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.SECOND), max_bids_count=1
        )
        assert speeds == (Hashrate(Decimal("1.00"), HashUnit.PH, TimeUnit.SECOND),)

    def test_uneven_split_quantized(self) -> None:
        """7/3 → three shares of 2.33 PH/s (rounded to 0.01)."""
        speeds = distribute_bids(
            Hashrate(Decimal("7"), HashUnit.PH, TimeUnit.SECOND), max_bids_count=3
        )
        assert speeds == (
            Hashrate(Decimal("2.33"), HashUnit.PH, TimeUnit.SECOND),
            Hashrate(Decimal("2.33"), HashUnit.PH, TimeUnit.SECOND),
            Hashrate(Decimal("2.33"), HashUnit.PH, TimeUnit.SECOND),
        )

    def test_max_bids_count_must_be_positive(self) -> None:
        """max_bids_count < 1 raises ValueError."""
        with pytest.raises(ValueError, match="max_bids_count"):
            distribute_bids(
                Hashrate(Decimal("5"), HashUnit.PH, TimeUnit.SECOND), max_bids_count=0
            )


class TestFindMarketPrice:
    """Tests for find_market_price."""

    def test_picks_lowest_served_plus_one(self) -> None:
        """Among served bids, picks the lowest price and adds 1 sat."""
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
        price = find_market_price(orderbook)
        assert price.sats == Sats(701)
        assert price.per == EH_DAY

    def test_single_served_bid(self) -> None:
        """A single served bid → that price + 1."""
        orderbook = OrderBook(
            bids=(_bid_item(price_sat=1234, hr_matched="0.5"),),
            asks=(),
        )
        price = find_market_price(orderbook)
        assert price.sats == Sats(1235)

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
            find_market_price(orderbook)

    def test_empty_orderbook_raises(self) -> None:
        """Empty bids tuple raises ValueError."""
        with pytest.raises(ValueError, match="no served bids"):
            find_market_price(OrderBook(bids=(), asks=()))


_NOW = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
_SETTINGS = MarketSettings(
    min_bid_price_decrease_period=timedelta(seconds=600),
    min_bid_speed_limit_decrease_period=timedelta(seconds=600),
)
DESIRED_PRICE = HashratePrice(sats=Sats(500), per=PH_DAY)


def _annotated(bid: object, price_cd: bool, speed_cd: bool) -> BidWithCooldown:
    return BidWithCooldown(
        bid=bid,  # type: ignore[arg-type]
        cooldown=CooldownInfo(price_cooldown=price_cd, speed_cooldown=speed_cd),
    )


class TestCheckCooldowns:
    """Tests for check_cooldowns."""

    def test_recent_bid_in_both_cooldowns(self) -> None:
        """A bid updated 10s ago is in both cooldown windows."""
        bid = make_user_bid("B1", 500, "5.0", last_updated=_NOW - timedelta(seconds=10))
        (entry,) = check_cooldowns((bid,), _SETTINGS, _NOW)
        assert entry.bid is bid
        assert entry.cooldown == CooldownInfo(price_cooldown=True, speed_cooldown=True)

    def test_old_bid_in_neither_cooldown(self) -> None:
        """A bid updated well past both cooldown windows is free."""
        bid = make_user_bid(
            "B1", 500, "5.0", last_updated=_NOW - timedelta(seconds=3600)
        )
        (entry,) = check_cooldowns((bid,), _SETTINGS, _NOW)
        assert entry.cooldown == CooldownInfo(
            price_cooldown=False, speed_cooldown=False
        )

    def test_distinct_windows(self) -> None:
        """Different periods can leave one cooldown active and the other not."""
        settings = MarketSettings(
            min_bid_price_decrease_period=timedelta(seconds=600),
            min_bid_speed_limit_decrease_period=timedelta(seconds=60),
        )
        bid = make_user_bid(
            "B1", 500, "5.0", last_updated=_NOW - timedelta(seconds=120)
        )
        (entry,) = check_cooldowns((bid,), settings, _NOW)
        assert entry.cooldown == CooldownInfo(price_cooldown=True, speed_cooldown=False)


class TestPlanWithCooldowns:
    """Tests for plan_with_cooldowns."""

    def test_no_cooldowns_matches_naive_distribution(self) -> None:
        """No cooldowns → result mirrors plain distribute_bids at desired_price."""
        result = plan_with_cooldowns(
            desired_price=DESIRED_PRICE,
            needed=_ph_s("5"),
            max_bids_count=3,
            bids=(),
        )
        assert len(result) == 3
        assert all(b.price == DESIRED_PRICE for b in result)
        # distribute_bids quantizes shares to 0.01 PH/s.
        total = sum((b.speed_limit.value for b in result), Decimal(0))
        assert abs(total - Decimal("5")) <= Decimal("0.03")

    def test_price_cooldown_only_keeps_old_price(self) -> None:
        """A price-locked bid keeps its price; speed comes from the distribution."""
        bid = make_user_bid("B1", 900, "2.0", last_updated=_NOW - timedelta(seconds=10))
        result = plan_with_cooldowns(
            desired_price=DESIRED_PRICE,
            needed=_ph_s("4"),
            max_bids_count=2,
            bids=(_annotated(bid, price_cd=True, speed_cd=False),),
        )
        assert len(result) == 2
        assert result[0].price == bid.price
        assert result[0].speed_limit == _ph_s("2")
        assert result[1].price == DESIRED_PRICE
        assert result[1].speed_limit == _ph_s("2")

    def test_speed_cooldown_freezes_speed_and_redistributes(self) -> None:
        """Speed-locked bid keeps its current speed; remainder goes to free slots."""
        bid = make_user_bid("B1", 500, "3.0", last_updated=_NOW - timedelta(seconds=10))
        result = plan_with_cooldowns(
            desired_price=DESIRED_PRICE,
            needed=_ph_s("5"),
            max_bids_count=3,
            bids=(_annotated(bid, price_cd=False, speed_cd=True),),
        )
        assert len(result) == 3
        assert result[0].speed_limit == _ph_s("3")
        assert result[0].price == DESIRED_PRICE  # not price-locked
        for entry in result[1:]:
            assert entry.price == DESIRED_PRICE
        free_total = sum((b.speed_limit.value for b in result[1:]), Decimal(0))
        assert free_total == Decimal("2")

    def test_both_cooldowns_freeze_bid_completely(self) -> None:
        """A fully-frozen bid keeps both fields and consumes a slot+budget."""
        bid = make_user_bid("B1", 900, "3.0", last_updated=_NOW - timedelta(seconds=10))
        result = plan_with_cooldowns(
            desired_price=DESIRED_PRICE,
            needed=_ph_s("5"),
            max_bids_count=3,
            bids=(_annotated(bid, price_cd=True, speed_cd=True),),
        )
        assert result[0].price == bid.price
        assert result[0].speed_limit == _ph_s("3")
        assert len(result) == 3
        for entry in result[1:]:
            assert entry.price == DESIRED_PRICE
        assert sum((b.speed_limit.value for b in result[1:]), Decimal(0)) == Decimal(
            "2"
        )

    def test_all_bids_in_cooldown_no_free_slots(self) -> None:
        """All slots taken by frozen bids: no new entries, no errors."""
        b1 = make_user_bid("B1", 800, "2.0", last_updated=_NOW - timedelta(seconds=10))
        b2 = make_user_bid("B2", 900, "3.0", last_updated=_NOW - timedelta(seconds=10))
        result = plan_with_cooldowns(
            desired_price=DESIRED_PRICE,
            needed=_ph_s("5"),
            max_bids_count=2,
            bids=(
                _annotated(b1, price_cd=True, speed_cd=True),
                _annotated(b2, price_cd=True, speed_cd=True),
            ),
        )
        assert len(result) == 2
        assert {r.price for r in result} == {b1.price, b2.price}

    def test_speed_lock_exceeds_needed_clamps_remainder(self) -> None:
        """Locked speed greater than needed leaves zero for free slots."""
        bid = make_user_bid(
            "B1", 500, "10.0", last_updated=_NOW - timedelta(seconds=10)
        )
        result = plan_with_cooldowns(
            desired_price=DESIRED_PRICE,
            needed=_ph_s("5"),
            max_bids_count=3,
            bids=(_annotated(bid, price_cd=False, speed_cd=True),),
        )
        # Only the locked bid; no extras since remaining is 0.
        assert len(result) == 1
        assert result[0].speed_limit == _ph_s("10")
