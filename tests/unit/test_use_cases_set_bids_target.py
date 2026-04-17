"""Tests for the set_bids_target use case orchestrator."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hashbidder.client import (
    ApiError,
    BidHistory,
    BidHistoryEntry,
    BidId,
    BidItem,
    MarketSettings,
    OrderBook,
)
from hashbidder.config import TargetHashrateConfig
from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.price_tick import PriceTick
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.ocean_client import AccountStats, HashrateWindow, OceanTimeWindow
from hashbidder.use_cases.set_bids_target import set_bids_target
from tests.conftest import UPSTREAM, FakeClient, FakeOceanSource, make_user_bid

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
        assert inputs.price.sats == Sats(801_000)

        plan = result.set_bids_result.plan
        assert len(plan.creates) == 3
        for create in plan.creates:
            assert create.config.price.sats == Sats(801_000)
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

    def test_speed_cooldown_locks_existing_bid(self) -> None:
        """A bid still in speed cooldown stays at its current speed in the plan."""
        now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
        cooldown_bid = make_user_bid(
            "B1", 800, "3.0", last_updated=now - timedelta(seconds=30)
        )
        client = FakeClient(
            orderbook=_orderbook(served_price_sat=500_000),
            current_bids=(cooldown_bid,),
            market_settings=MarketSettings(
                min_bid_price_decrease_period=timedelta(seconds=600),
                min_bid_speed_limit_decrease_period=timedelta(seconds=600),
                price_tick=PriceTick(sats=Sats(1000)),
            ),
        )
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(
            client, ocean, ADDRESS, _config("10"), dry_run=True, now=now
        )

        # The locked bid (3 PH/s) should appear unchanged in the plan; the
        # remainder (15-3=12 PH/s) is split across the other 2 slots.
        plan = result.set_bids_result.plan
        # B1 is matched: edit if its price differs from desired, else unchanged.
        # Desired price = 500_001 sat/EH/Day = 500.001 sat/PH/Day, current is
        # 800 sat/PH/Day = 800_000 sat/EH/Day. Price cooldown is also active,
        # so plan_with_cooldowns leaves the price untouched at 800.
        assert len(plan.unchanged) == 1
        assert plan.unchanged[0].bid is cooldown_bid
        # Two new creates at 6 PH/s each (12 / 2).
        assert len(plan.creates) == 2
        for create in plan.creates:
            assert create.config.speed_limit == _ph_s("6")

    def test_price_cooldown_only_keeps_price_speed_freely_assigned(self) -> None:
        """Price-only cooldown: bid keeps its price; speed comes from distribution."""
        now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
        cooldown_bid = make_user_bid(
            "B1", 800, "4.0", last_updated=now - timedelta(seconds=30)
        )
        client = FakeClient(
            orderbook=_orderbook(served_price_sat=500_000),
            current_bids=(cooldown_bid,),
            market_settings=MarketSettings(
                min_bid_price_decrease_period=timedelta(seconds=600),
                min_bid_speed_limit_decrease_period=timedelta(seconds=10),
                price_tick=PriceTick(sats=Sats(1000)),
            ),
        )
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(
            client, ocean, ADDRESS, _config("10"), dry_run=True, now=now
        )

        # needed=15, 3 free slots → 5 PH/s each. B1 keeps price 800, takes 5 PH/s.
        plan = result.set_bids_result.plan
        assert len(plan.edits) == 1
        edit = plan.edits[0]
        assert edit.bid is cooldown_bid
        assert not edit.price_changed  # price preserved
        assert edit.speed_limit_changed
        assert edit.new_speed_limit_ph == _ph_s("5")
        # Two new creates at the market price.
        assert len(plan.creates) == 2
        for create in plan.creates:
            assert create.config.speed_limit == _ph_s("5")
            assert create.config.price.sats == Sats(501_000)
        assert plan.cancels == ()

    def test_both_cooldowns_lock_price_and_speed(self) -> None:
        """Both cooldowns: bid is fully frozen; remainder distributes to free slots."""
        now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
        cooldown_bid = make_user_bid(
            "B1", 900, "4.0", last_updated=now - timedelta(seconds=30)
        )
        client = FakeClient(
            orderbook=_orderbook(served_price_sat=500_000),
            current_bids=(cooldown_bid,),
            market_settings=MarketSettings(
                min_bid_price_decrease_period=timedelta(seconds=600),
                min_bid_speed_limit_decrease_period=timedelta(seconds=600),
                price_tick=PriceTick(sats=Sats(1000)),
            ),
        )
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(
            client, ocean, ADDRESS, _config("10"), dry_run=True, now=now
        )

        # B1 stays at (900, 4); remaining 11 PH/s split across 2 new slots.
        plan = result.set_bids_result.plan
        assert len(plan.unchanged) == 1
        assert plan.unchanged[0].bid is cooldown_bid
        assert plan.edits == ()
        assert len(plan.creates) == 2
        for create in plan.creates:
            assert create.config.price.sats == Sats(501_000)
        free_total = sum((c.config.speed_limit.value for c in plan.creates), Decimal(0))
        # distribute_bids quantizes to 0.01 PH/s (5.5 + 5.5 = 11).
        assert abs(free_total - Decimal("11")) <= Decimal("0.02")
        assert plan.cancels == ()

    def test_all_bids_locked_no_new_creates(self) -> None:
        """Extreme: every existing bid is fully frozen and fills max_bids_count."""
        now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
        bids = (
            make_user_bid("B1", 600, "2.0", last_updated=now - timedelta(seconds=30)),
            make_user_bid("B2", 700, "3.0", last_updated=now - timedelta(seconds=30)),
            make_user_bid("B3", 800, "5.0", last_updated=now - timedelta(seconds=30)),
        )
        client = FakeClient(
            orderbook=_orderbook(served_price_sat=500_000),
            current_bids=bids,
            market_settings=MarketSettings(
                min_bid_price_decrease_period=timedelta(seconds=600),
                min_bid_speed_limit_decrease_period=timedelta(seconds=600),
                price_tick=PriceTick(sats=Sats(1000)),
            ),
        )
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(
            client, ocean, ADDRESS, _config("10"), dry_run=True, now=now
        )

        # All slots consumed by frozen bids; reconciler sees an exact match
        # for each → no edits, no creates, no cancels.
        plan = result.set_bids_result.plan
        assert len(plan.unchanged) == 3
        assert {u.bid.id for u in plan.unchanged} == {b.id for b in bids}
        assert plan.edits == ()
        assert plan.creates == ()
        assert plan.cancels == ()

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


_DETAIL_SETTINGS = MarketSettings(
    min_bid_price_decrease_period=timedelta(seconds=600),
    min_bid_speed_limit_decrease_period=timedelta(seconds=600),
    price_tick=PriceTick(sats=Sats(1000)),
)


def _history(entries: tuple[BidHistoryEntry, ...]) -> BidHistory:
    return BidHistory(entries=entries)


def _entry(t: datetime, price_sat_per_eh_day: int, speed: str) -> BidHistoryEntry:
    return BidHistoryEntry(
        timestamp=t,
        price=HashratePrice(sats=Sats(price_sat_per_eh_day), per=EH_DAY),
        speed_limit_ph=_ph_s(speed),
    )


def _detail_call_count(client: FakeClient) -> int:
    return sum(1 for call in client.calls if call[0] == "get_bid_detail")


class TestHistoryFetchWiring:
    """Tests for the tier-1 / tier-2 wiring inside set_bids_target."""

    def test_not_in_cooldown_bids_incur_zero_history_fetches(self) -> None:
        """All bids past both decrease windows → no /spot/bid/detail calls."""
        now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
        old = now - timedelta(seconds=3600)
        bids = (
            make_user_bid("B1", 600, "2.0", last_updated=old),
            make_user_bid("B2", 700, "3.0", last_updated=old),
        )
        client = FakeClient(
            orderbook=_orderbook(served_price_sat=500_000),
            current_bids=bids,
            market_settings=_DETAIL_SETTINGS,
        )
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        set_bids_target(client, ocean, ADDRESS, _config("10"), dry_run=True, now=now)

        assert _detail_call_count(client) == 0

    def test_recent_increase_only_history_clears_both_flags(self) -> None:
        """Tier-1 ambiguous + history shows only an increase → bid is free."""
        now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
        bid = make_user_bid("B1", 501, "3.0", last_updated=now - timedelta(seconds=30))
        # Price and speed strictly increased — nothing to cool for.
        history = _history(
            (
                _entry(now - timedelta(seconds=3600), 400_000, "2"),
                _entry(now - timedelta(seconds=60), 501_000, "3"),
            )
        )
        client = FakeClient(
            orderbook=_orderbook(served_price_sat=500_000),
            current_bids=(bid,),
            market_settings=_DETAIL_SETTINGS,
            bid_histories={BidId("B1"): history},
        )
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(
            client, ocean, ADDRESS, _config("10"), dry_run=True, now=now
        )

        assert _detail_call_count(client) == 1
        (annotated,) = result.inputs.annotated_bids
        assert annotated.cooldown.price_cooldown is False
        assert annotated.cooldown.speed_cooldown is False

    def test_recent_speed_decrease_sets_speed_cooldown_only(self) -> None:
        """Tier-1 ambiguous + history shows a speed decrease → speed locked only."""
        now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
        bid = make_user_bid("B1", 501, "3.0", last_updated=now - timedelta(seconds=30))
        history = _history(
            (
                _entry(now - timedelta(seconds=3600), 501_000, "6"),
                _entry(now - timedelta(seconds=60), 501_000, "3"),
            )
        )
        client = FakeClient(
            orderbook=_orderbook(served_price_sat=500_000),
            current_bids=(bid,),
            market_settings=_DETAIL_SETTINGS,
            bid_histories={BidId("B1"): history},
        )
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(
            client, ocean, ADDRESS, _config("10"), dry_run=True, now=now
        )

        assert _detail_call_count(client) == 1
        (annotated,) = result.inputs.annotated_bids
        assert annotated.cooldown.speed_cooldown is True
        assert annotated.cooldown.price_cooldown is False

    def test_api_error_on_detail_falls_back_to_tier1(self) -> None:
        """History fetch failure → conservative tier-1 flags, no crash."""
        now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
        bid = make_user_bid("B1", 501, "3.0", last_updated=now - timedelta(seconds=30))
        client = FakeClient(
            orderbook=_orderbook(served_price_sat=500_000),
            current_bids=(bid,),
            market_settings=_DETAIL_SETTINGS,
            # No seeded history → get_bid_detail raises ApiError 404.
            errors={("get_bid_detail", "B1"): [ApiError(500, "boom")]},
        )
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(
            client, ocean, ADDRESS, _config("10"), dry_run=True, now=now
        )

        assert _detail_call_count(client) == 1
        (annotated,) = result.inputs.annotated_bids
        # Conservative fallback: bid is within both decrease windows → both True.
        assert annotated.cooldown.price_cooldown is True
        assert annotated.cooldown.speed_cooldown is True
