"""Tests for the set_bids_target use case orchestrator."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hashbidder.clients.braiins import (
    ApiError,
    BidHistory,
    BidHistoryEntry,
    BidId,
    BidItem,
    BidStatus,
    MarketSettings,
    OrderBook,
)
from hashbidder.clients.ocean import AccountStats, HashrateWindow, OceanTimeWindow
from hashbidder.domain.bid_config import TargetHashrateConfig
from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.price_tick import PriceTick
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
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


def _config(target_ph_s: str) -> TargetHashrateConfig:
    return TargetHashrateConfig(
        default_amount=Sats(100_000),
        upstream=UPSTREAM,
        target_hashrate=_ph_s(target_ph_s),
    )


class TestSetBidsTarget:
    """Integration tests wiring observe → plan → apply.

    Detailed planner semantics live in test_plan_reconciliation.py.
    """

    def test_happy_path_below_target_creates_bids(self) -> None:
        """Below target → plan creates one bid at market price + 1."""
        client = FakeClient(orderbook=_orderbook(served_price_sat=800_000))
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(client, ocean, ADDRESS, _config("10"), dry_run=True)

        inputs = result.inputs
        assert inputs.ocean_24h == _ph_s("5")
        assert inputs.target == _ph_s("10")
        assert inputs.needed_hashrate == _ph_s("45")
        assert inputs.price.sats == Sats(801_000)

        plan = result.set_bids_result.plan
        assert len(plan.creates) == 1
        create = plan.creates[0]
        assert create.config.price.sats == Sats(801_000)
        assert create.config.speed_limit == _ph_s("45")

    def test_non_manageable_bids_flow_to_skipped_bids(self) -> None:
        """PAUSED/FROZEN bids bypass planning and pass through for display."""
        paused = make_user_bid("B1", 700, "2.0", status=BidStatus.PAUSED)
        frozen = make_user_bid("B2", 800, "4.0", status=BidStatus.FROZEN)
        active = make_user_bid("B3", 501, "45.0")
        client = FakeClient(
            orderbook=_orderbook(served_price_sat=500_000),
            current_bids=(paused, frozen, active),
        )
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(client, ocean, ADDRESS, _config("10"), dry_run=True)

        assert {b.id for b in result.set_bids_result.skipped_bids} == {
            paused.id,
            frozen.id,
        }
        # Only the ACTIVE bid reaches the planner (and already aligns with target).
        plan = result.set_bids_result.plan
        assert plan.unchanged == (active,)
        assert plan.cancels == ()
        assert plan.edits == ()
        assert plan.creates == ()

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


def _history_call_count(client: FakeClient) -> int:
    return sum(1 for call in client.calls if call[0] == "get_bid_history")


class TestHistoryFetchWiring:
    """Tests for the tier-1 / tier-2 wiring inside set_bids_target."""

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

        assert _history_call_count(client) == 1
        (annotated,) = result.inputs.bids_with_cooldowns
        assert annotated.is_price_in_cooldown is False
        assert annotated.is_speed_in_cooldown is False

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

        assert _history_call_count(client) == 1
        (annotated,) = result.inputs.bids_with_cooldowns
        assert annotated.is_speed_in_cooldown is True
        assert annotated.is_price_in_cooldown is False

    def test_api_error_on_detail_falls_back_to_tier1(self) -> None:
        """History fetch failure → conservative tier-1 flags, no crash."""
        now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
        bid = make_user_bid("B1", 501, "3.0", last_updated=now - timedelta(seconds=30))
        client = FakeClient(
            orderbook=_orderbook(served_price_sat=500_000),
            current_bids=(bid,),
            market_settings=_DETAIL_SETTINGS,
            # No seeded history → get_bid_history raises ApiError 404.
            errors={("get_bid_history", "B1"): [ApiError(500, "boom")]},
        )
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(
            client, ocean, ADDRESS, _config("10"), dry_run=True, now=now
        )

        assert _history_call_count(client) == 1
        (annotated,) = result.inputs.bids_with_cooldowns
        # Conservative fallback: bid is within both decrease windows → both True.
        assert annotated.is_price_in_cooldown is True
        assert annotated.is_speed_in_cooldown is True


class TestRegressionProxyFalsePositive:
    """Direct regression for the 2026-04-17 09:52:02 incident.

    A recent non-decrease edit (price increase only) previously bumped
    ``last_updated`` and the proxy-only check pinned both flags True,
    leaving the bid frozen even though the server would have accepted a
    decrease on either field. Tier-2 history must override the proxy and
    free the bid so the planner can lower it.
    """

    def test_recent_price_increase_only_does_not_pin_bid(self) -> None:
        """Proxy flags both True; history shows only a price rise → bid is free."""
        now = datetime(2026, 4, 17, 9, 52, 2, tzinfo=UTC)
        # last_updated = 2 minutes ago → well inside both 600s decrease windows.
        bid = make_user_bid(
            "B86609956915618911",
            900,
            "4.0",
            last_updated=now - timedelta(seconds=120),
        )
        # Only a price increase (800_000 → 900_000 sat/EH/Day) within the window;
        # speed unchanged. No decrease of either field.
        history = _history(
            (
                _entry(now - timedelta(seconds=3600), 800_000, "4"),
                _entry(now - timedelta(seconds=120), 900_000, "4"),
            )
        )
        client = FakeClient(
            orderbook=_orderbook(served_price_sat=500_000),
            current_bids=(bid,),
            market_settings=_DETAIL_SETTINGS,
            bid_histories={BidId("B86609956915618911"): history},
        )
        ocean = FakeOceanSource(account_stats=_account_stats("5"))

        result = set_bids_target(
            client, ocean, ADDRESS, _config("10"), dry_run=True, now=now
        )

        # Tier-2 consulted once and cleared both flags.
        assert _history_call_count(client) == 1
        (annotated,) = result.inputs.bids_with_cooldowns
        assert annotated.is_price_in_cooldown is False
        assert annotated.is_speed_in_cooldown is False

        # The bid is no longer pinned: planner edits it down to the market
        # price and the full needed hashrate (single-bid convergence).
        plan = result.set_bids_result.plan
        assert plan.unchanged == ()
        assert plan.creates == ()
        assert plan.cancels == ()
        assert len(plan.edits) == 1
        edit = plan.edits[0]
        assert edit.bid is bid
        assert edit.price_changed
        assert edit.new_price.sats == Sats(501_000)
        assert edit.speed_limit_changed
        assert edit.new_speed_limit_ph == _ph_s("45")
