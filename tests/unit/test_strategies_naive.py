"""Tests for the naive cooldown-aware bid planner."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.strategies.naive import plan
from hashbidder.target_hashrate import BidWithCooldown, CooldownInfo
from tests.conftest import make_user_bid

PH_DAY = Hashrate(Decimal(1), HashUnit.PH, TimeUnit.DAY)


def _ph_s(value: str) -> Hashrate:
    return Hashrate(Decimal(value), HashUnit.PH, TimeUnit.SECOND)


_NOW = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
DESIRED_PRICE = HashratePrice(sats=Sats(500), per=PH_DAY)


def _annotated(bid: object, price_cd: bool, speed_cd: bool) -> BidWithCooldown:
    return BidWithCooldown(
        bid=bid,  # type: ignore[arg-type]
        cooldown=CooldownInfo(price_cooldown=price_cd, speed_cooldown=speed_cd),
    )


class TestNaivePlan:
    """Tests for the naive strategy's plan function."""

    def test_no_cooldowns_matches_naive_distribution(self) -> None:
        """No cooldowns → result mirrors plain distribute_bids at target_price."""
        result = plan(
            target_price=DESIRED_PRICE,
            target_hashrate=_ph_s("5"),
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
        result = plan(
            target_price=DESIRED_PRICE,
            target_hashrate=_ph_s("4"),
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
        result = plan(
            target_price=DESIRED_PRICE,
            target_hashrate=_ph_s("5"),
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
        result = plan(
            target_price=DESIRED_PRICE,
            target_hashrate=_ph_s("5"),
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
        result = plan(
            target_price=DESIRED_PRICE,
            target_hashrate=_ph_s("5"),
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
        result = plan(
            target_price=DESIRED_PRICE,
            target_hashrate=_ph_s("5"),
            max_bids_count=3,
            bids=(_annotated(bid, price_cd=False, speed_cd=True),),
        )
        # Only the locked bid; no extras since remaining is 0.
        assert len(result) == 1
        assert result[0].speed_limit == _ph_s("10")
