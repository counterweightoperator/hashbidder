"""Tests for the prioritized-bucket bid planner."""

from decimal import Decimal

from hashbidder.domain.bid_config import BidConfig
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.strategies.prioritized_buckets import plan
from hashbidder.target_hashrate import BidWithCooldown, CooldownInfo
from tests.conftest import make_user_bid

PH_DAY = Hashrate(Decimal(1), HashUnit.PH, TimeUnit.DAY)


def _ph_s(value: str) -> Hashrate:
    return Hashrate(Decimal(value), HashUnit.PH, TimeUnit.SECOND)


def _price(sats: int) -> HashratePrice:
    return HashratePrice(sats=Sats(sats), per=PH_DAY)


def _bwc(
    price_sats: int,
    speed_ph: str,
    *,
    price_locked: bool = False,
    speed_locked: bool = False,
    bid_id: str = "b1",
) -> BidWithCooldown:
    return BidWithCooldown(
        bid=make_user_bid(
            bid_id=bid_id, price_sat_per_ph_day=price_sats, speed=speed_ph
        ),
        cooldown=CooldownInfo(price_cooldown=price_locked, speed_cooldown=speed_locked),
    )


def _total_speed(configs: tuple[BidConfig, ...]) -> Decimal:
    return sum(
        (c.speed_limit.to(HashUnit.PH, TimeUnit.SECOND).value for c in configs),
        Decimal(0),
    )


TARGET = _price(500)


class TestPricing:
    """Phase 1: each bid's price reflects the target when not price-locked."""

    def test_empty_book_fills_with_target_price_creates(self) -> None:
        """No existing bids → max_bids_count creates all at target price."""
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("9"),
            max_bids_count=3,
            bids=(),
        )
        assert len(result) == 3
        assert all(c.price == TARGET for c in result)

    def test_above_target_price_free_is_lowered_to_target(self) -> None:
        """Overpriced but price-free → config price drops to target."""
        bid = _bwc(1500, "3", price_locked=False, speed_locked=False)
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("6"),
            max_bids_count=3,
            bids=(bid,),
        )
        assert all(c.price == TARGET for c in result)

    def test_above_target_price_locked_keeps_original_price(self) -> None:
        """Overpriced + price-locked → config keeps its current high price."""
        bid = _bwc(1500, "3", price_locked=True, speed_locked=False)
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("6"),
            max_bids_count=3,
            bids=(bid,),
        )
        locked = [c for c in result if c.price == bid.bid.price]
        assert len(locked) == 1

    def test_ok_price_locked_is_raised_to_target(self) -> None:
        """Underpriced + price-locked → raise to target (raises are allowed)."""
        bid = _bwc(200, "3", price_locked=True, speed_locked=False)
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("6"),
            max_bids_count=3,
            bids=(bid,),
        )
        assert all(c.price == TARGET for c in result)


class TestCancel:
    """Phase 2: cancel only when the min achievable hashrate exceeds target."""

    def test_no_cancels_when_min_reaches_target(self) -> None:
        """Min = 3 PH/s (three speed-free bids), target = 10 → all kept."""
        bids = (
            _bwc(500, "5", bid_id="a"),
            _bwc(500, "5", bid_id="b"),
            _bwc(500, "5", bid_id="c"),
        )
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("10"),
            max_bids_count=3,
            bids=bids,
        )
        assert len(result) == 3

    def test_drops_largest_speed_locked_first_within_bucket(self) -> None:
        """Three speed-locked bids (10, 3, 7), target 5 → keep only the 3."""
        bids = (
            _bwc(500, "10", speed_locked=True, bid_id="a"),
            _bwc(500, "3", speed_locked=True, bid_id="b"),
            _bwc(500, "7", speed_locked=True, bid_id="c"),
        )
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("5"),
            max_bids_count=3,
            bids=bids,
        )
        speeds = sorted(
            c.speed_limit.to(HashUnit.PH, TimeUnit.SECOND).value for c in result
        )
        assert Decimal("3") in speeds
        assert Decimal("10") not in speeds
        assert Decimal("7") not in speeds

    def test_above_target_cancelled_before_at_or_below(self) -> None:
        """Cancel priority drops overpriced bids before OK-priced ones."""
        expensive = _bwc(1500, "5", price_locked=True, speed_locked=True, bid_id="bad")
        cheap = _bwc(400, "7", price_locked=True, speed_locked=True, bid_id="ok")
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("10"),
            max_bids_count=3,
            bids=(expensive, cheap),
        )
        prices_kept = {c.price.to(HashUnit.PH, TimeUnit.DAY).sats for c in result}
        assert Sats(1500) not in prices_kept
        # Cheap survives with its speed-locked 7 PH/s; OK-priced locked bids
        # are raised to target, so price=400 is not expected.
        assert any(c.speed_limit == _ph_s("7") for c in result)


class TestSpeedAllocation:
    """Phase 3: allocate fixed speeds, shrink bad-assignable, distribute rest."""

    def test_speed_locked_bid_keeps_current_speed(self) -> None:
        """Speed-locked 3 PH/s stays at 3 PH/s in the output."""
        bid = _bwc(500, "3", speed_locked=True)
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("10"),
            max_bids_count=3,
            bids=(bid,),
        )
        locked = [c for c in result if c.speed_limit == _ph_s("3")]
        assert len(locked) == 1

    def test_above_locked_speed_free_shrinks_to_one_phs(self) -> None:
        """Overpriced + price-locked + speed-free → speed shrunk to 1 PH/s."""
        bid = _bwc(1500, "20", price_locked=True, speed_locked=False)
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("10"),
            max_bids_count=3,
            bids=(bid,),
        )
        at_bad_price = [c for c in result if c.price == bid.bid.price]
        assert len(at_bad_price) == 1
        assert at_bad_price[0].speed_limit == _ph_s("1")

    def test_remaining_budget_distributed_across_free_slots(self) -> None:
        """After a 2 PH/s speed-locked bid, remaining 8 PH/s fills 2 free slots."""
        bid = _bwc(500, "2", speed_locked=True)
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("10"),
            max_bids_count=3,
            bids=(bid,),
        )
        assert len(result) == 3
        free = [c for c in result if c.speed_limit != _ph_s("2")]
        free_total = sum(
            (c.speed_limit.to(HashUnit.PH, TimeUnit.SECOND).value for c in free),
            Decimal(0),
        )
        assert abs(free_total - Decimal("8")) <= Decimal("0.03")

    def test_locked_speed_exactly_at_target_uses_no_extra_slots(self) -> None:
        """A speed-locked bid at exactly the target leaves no remaining budget."""
        bid = _bwc(500, "5", speed_locked=True)
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("5"),
            max_bids_count=3,
            bids=(bid,),
        )
        assert len(result) == 1
        assert result[0].speed_limit == _ph_s("5")


class TestIntegration:
    """Cross-cutting scenarios that exercise all three phases."""

    def test_mixed_book_produces_full_plan(self) -> None:
        """Speed-locked + price-locked + price-free bids share the budget."""
        speed_locked = _bwc(500, "2", speed_locked=True, bid_id="sl")
        price_locked_above = _bwc(
            1500, "5", price_locked=True, speed_locked=False, bid_id="plp"
        )
        free = _bwc(400, "4", bid_id="free")
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("10"),
            max_bids_count=4,
            bids=(speed_locked, price_locked_above, free),
        )
        # Speed-locked survives with 2 PH/s.
        assert any(c.speed_limit == _ph_s("2") for c in result)
        # Price-locked-above keeps its price, shrunk to 1 PH/s.
        bad = [c for c in result if c.price == price_locked_above.bid.price]
        assert len(bad) == 1
        assert bad[0].speed_limit == _ph_s("1")
        # Total speed is at least the committed portion (2 + 1 = 3).
        assert _total_speed(result) >= Decimal("3")

    def test_never_returns_more_than_max_bids_count(self) -> None:
        """Output length never exceeds max_bids_count."""
        bids = tuple(_bwc(500, "1", bid_id=f"b{i}") for i in range(5))
        result = plan(
            target_price=TARGET,
            target_hashrate=_ph_s("10"),
            max_bids_count=3,
            bids=bids,
        )
        assert len(result) <= 3
