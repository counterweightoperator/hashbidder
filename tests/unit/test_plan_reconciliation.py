"""Direct tests for _plan_reconciliation — the pure planning logic."""

from decimal import Decimal

from hashbidder.clients.braiins import AccountBalance
from hashbidder.domain.bid_config import TargetHashrateConfig
from hashbidder.domain.bid_planning import CancelReason
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.services.target_hashrate import BidWithCooldown
from hashbidder.use_cases.set_bids_target import (
    TargetHashrateInputs,
    _plan_reconciliation,
)
from tests.conftest import UPSTREAM, make_user_bid

EH_DAY = Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY)


def _ph_s(value: str) -> Hashrate:
    return Hashrate(Decimal(value), HashUnit.PH, TimeUnit.SECOND)


def _price(sats: int) -> HashratePrice:
    return HashratePrice(sats=Sats(sats), per=EH_DAY)


_DEFAULT_NEEDED = _ph_s("15")
_DEFAULT_PRICE = HashratePrice(sats=Sats(501_000), per=EH_DAY)


def _config() -> TargetHashrateConfig:
    return TargetHashrateConfig(
        default_amount=Sats(100_000),
        upstream=UPSTREAM,
        target_hashrate=_ph_s("10"),
    )


_ZERO_BALANCE = AccountBalance(
    available_sat=Sats(0), blocked_sat=Sats(0), total_sat=Sats(0)
)


def _inputs(
    *,
    needed: Hashrate = _DEFAULT_NEEDED,
    price: HashratePrice = _DEFAULT_PRICE,
    bids_with_cooldowns: tuple[BidWithCooldown, ...] = (),
) -> TargetHashrateInputs:
    return TargetHashrateInputs(
        ocean_24h=_ph_s("5"),
        target=_ph_s("10"),
        needed_hashrate=needed,
        target_price=price,
        bids_with_cooldowns=bids_with_cooldowns,
        non_manageable_bids=(),
        available_balance=_ZERO_BALANCE,
    )


def _bwc(
    bid_id: str = "B1",
    price_sat_per_ph_day: int = 800,
    speed: str = "3",
    *,
    price_cd: bool = False,
    speed_cd: bool = False,
    amount: int = 100_000,
    remaining: int | None = None,
) -> BidWithCooldown:
    return BidWithCooldown(
        bid=make_user_bid(
            bid_id, price_sat_per_ph_day, speed, amount=amount, remaining=remaining
        ),
        is_price_in_cooldown=price_cd,
        is_speed_in_cooldown=speed_cd,
    )


class TestNeedZero:
    """When needed hashrate is zero: cancel everything manageable."""

    def test_no_bids_produces_empty_plan(self) -> None:
        """No existing bids + zero need → empty plan."""
        plan = _plan_reconciliation(
            _inputs(needed=_ph_s("0"), bids_with_cooldowns=()),
            _config(),
        )
        assert plan.cancels == ()
        assert plan.creates == ()
        assert plan.edits == ()
        assert plan.unchanged == ()

    def test_cancels_every_bid_with_need_zero_hashrate_reason(self) -> None:
        """Existing bids + zero need → cancel all with NEED_ZERO_HASHRATE."""
        bids = (
            _bwc("B1", 600, "2"),
            _bwc("B2", 700, "3", price_cd=True, speed_cd=True),
            _bwc("B3", 800, "5", speed_cd=True),
        )
        plan = _plan_reconciliation(
            _inputs(needed=_ph_s("0"), bids_with_cooldowns=bids),
            _config(),
        )
        assert {c.bid.id for c in plan.cancels} == {b.bid.id for b in bids}
        assert all(c.reason is CancelReason.NEED_ZERO_HASHRATE for c in plan.cancels)
        assert plan.creates == ()
        assert plan.edits == ()
        assert plan.unchanged == ()


class TestCreate:
    """No existing bids + positive need → create one at market price."""

    def test_creates_one_bid_at_market_price_and_needed_speed(self) -> None:
        """No bids + positive need → one create at (price, needed)."""
        plan = _plan_reconciliation(
            _inputs(
                needed=_ph_s("15"),
                price=_price(501_000),
                bids_with_cooldowns=(),
            ),
            _config(),
        )
        assert len(plan.creates) == 1
        create = plan.creates[0]
        assert create.config.price == _price(501_000)
        assert create.config.speed_limit == _ph_s("15")
        assert create.amount == Sats(100_000)
        assert create.upstream == UPSTREAM
        assert plan.cancels == ()
        assert plan.edits == ()
        assert plan.unchanged == ()


class TestSingleBidConvergence:
    """Per-field convergence: cooldowns block decreases only."""

    def test_fully_flexible_aligned_bid_is_unchanged(self) -> None:
        """Keeper already at (price, needed) → unchanged, no edit."""
        bid = _bwc(price_sat_per_ph_day=501, speed="15")
        plan = _plan_reconciliation(
            _inputs(bids_with_cooldowns=(bid,)),
            _config(),
        )
        assert plan.unchanged == (bid.bid,)
        assert plan.edits == ()

    def test_fully_flexible_misaligned_bid_is_edited_to_target(self) -> None:
        """No cooldowns + misaligned keeper → edit to (price, needed)."""
        bid = _bwc(price_sat_per_ph_day=800, speed="3")
        plan = _plan_reconciliation(
            _inputs(bids_with_cooldowns=(bid,)),
            _config(),
        )
        assert len(plan.edits) == 1
        edit = plan.edits[0]
        assert edit.new_price == _price(501_000)
        assert edit.new_speed_limit_ph == _ph_s("15")
        assert plan.unchanged == ()

    def test_price_cooldown_blocks_price_decrease(self) -> None:
        """Price cooldown + desired price < current → keep current price, edit speed."""
        bid = _bwc(price_sat_per_ph_day=800, speed="3", price_cd=True)
        plan = _plan_reconciliation(
            _inputs(bids_with_cooldowns=(bid,)),
            _config(),
        )
        edit = plan.edits[0]
        assert edit.new_price == bid.bid.price
        assert edit.new_speed_limit_ph == _ph_s("15")

    def test_price_cooldown_allows_price_increase(self) -> None:
        """Price cooldown allows an increase through to the target."""
        bid = _bwc(price_sat_per_ph_day=400, speed="3", price_cd=True)
        plan = _plan_reconciliation(
            _inputs(bids_with_cooldowns=(bid,)),
            _config(),
        )
        edit = plan.edits[0]
        assert edit.new_price == _price(501_000)
        assert edit.new_speed_limit_ph == _ph_s("15")

    def test_speed_cooldown_blocks_speed_decrease(self) -> None:
        """Speed cooldown + desired speed < current → keep current speed, edit price."""
        bid = _bwc(price_sat_per_ph_day=800, speed="20", speed_cd=True)
        plan = _plan_reconciliation(
            _inputs(bids_with_cooldowns=(bid,)),
            _config(),
        )
        edit = plan.edits[0]
        assert edit.new_price == _price(501_000)
        assert edit.new_speed_limit_ph == bid.bid.speed_limit_ph

    def test_speed_cooldown_allows_speed_increase(self) -> None:
        """Speed cooldown allows an increase through to the target."""
        bid = _bwc(price_sat_per_ph_day=800, speed="3", speed_cd=True)
        plan = _plan_reconciliation(
            _inputs(bids_with_cooldowns=(bid,)),
            _config(),
        )
        edit = plan.edits[0]
        assert edit.new_price == _price(501_000)
        assert edit.new_speed_limit_ph == _ph_s("15")

    def test_both_cooldowns_with_both_decreases_leaves_bid_unchanged(self) -> None:
        """Both cooldowns + both decreases → unchanged, no edit."""
        bid = _bwc(price_sat_per_ph_day=900, speed="20", price_cd=True, speed_cd=True)
        plan = _plan_reconciliation(
            _inputs(bids_with_cooldowns=(bid,)),
            _config(),
        )
        assert plan.unchanged == (bid.bid,)
        assert plan.edits == ()

    def test_both_cooldowns_still_allow_an_increase_on_one_field(self) -> None:
        """Both cooldowns: the increasing field moves; the decreasing one holds."""
        bid = _bwc(price_sat_per_ph_day=900, speed="3", price_cd=True, speed_cd=True)
        plan = _plan_reconciliation(
            _inputs(bids_with_cooldowns=(bid,)),
            _config(),
        )
        edit = plan.edits[0]
        assert edit.new_price == bid.bid.price
        assert edit.new_speed_limit_ph == _ph_s("15")


class TestKeeperSelection:
    """Multi-bid reduction: cancel extras and edit the keeper."""

    def test_cancel_extras_with_too_many_bids_reason(self) -> None:
        """Multi-bid → cancel extras with TOO_MANY_BIDS reason."""
        bids = (
            _bwc("B1", 600, "2"),
            _bwc("B2", 700, "3"),
            _bwc("B3", 800, "5"),
        )
        plan = _plan_reconciliation(
            _inputs(bids_with_cooldowns=bids),
            _config(),
        )
        assert len(plan.cancels) == 2
        assert all(c.reason is CancelReason.TOO_MANY_BIDS for c in plan.cancels)

    def test_prefers_least_locked_over_larger_remaining(self) -> None:
        """Keeper chosen by lock count (primary), not by amount."""
        # B1 big but doubly-locked; B2 small but fully free → keep B2.
        bids = (
            _bwc("B1", 600, "2", price_cd=True, speed_cd=True, amount=1_000_000),
            _bwc("B2", 700, "3", amount=10_000),
            _bwc("B3", 800, "5", speed_cd=True, amount=500_000),
        )
        plan = _plan_reconciliation(
            _inputs(bids_with_cooldowns=bids),
            _config(),
        )
        assert {c.bid.id for c in plan.cancels} == {bids[0].bid.id, bids[2].bid.id}

    def test_breaks_lock_ties_by_largest_remaining(self) -> None:
        """Equally locked bids → keeper is the one with the largest remaining."""
        # All equally locked → keeper is the one with biggest remaining amount.
        bids = (
            _bwc("B1", 600, "2", price_cd=True, speed_cd=True, remaining=100_000),
            _bwc("B2", 700, "3", price_cd=True, speed_cd=True, remaining=400_000),
            _bwc("B3", 800, "5", price_cd=True, speed_cd=True, remaining=250_000),
        )
        plan = _plan_reconciliation(
            _inputs(bids_with_cooldowns=bids),
            _config(),
        )
        assert {c.bid.id for c in plan.cancels} == {bids[0].bid.id, bids[2].bid.id}
