"""Tests for craft_all_possible_plans — the candidate-plan space generator."""

from decimal import Decimal
from fractions import Fraction

from hashbidder.clients.braiins import AccountBalance
from hashbidder.domain.bid_config import MIN_BID_SPEED_LIMIT, TargetHashrateConfig
from hashbidder.domain.bid_planning import ReconciliationPlan
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.services.target_hashrate import BidWithCooldown
from hashbidder.use_cases.set_bids_target import (
    _NEW_BID_TARGET_FRACTIONS_PERCENT,
    _SPEED_DECREASE_PERCENTS,
    _SPEED_INCREASE_PERCENTS,
    TargetHashrateInputs,
    craft_all_possible_plans,
)
from tests.conftest import UPSTREAM, make_user_bid


def _plan_key(plan: ReconciliationPlan) -> tuple[object, ...]:
    """Hashable structural key for a plan, for fast dedup in tests."""
    return (
        tuple((c.bid.id, c.reason.value) for c in plan.cancels),
        tuple((e.bid.id, e.new_price, e.new_speed_limit_ph) for e in plan.edits),
        tuple(u.id for u in plan.unchanged),
        tuple((c.config.price, c.config.speed_limit, c.upstream) for c in plan.creates),
    )


EH_DAY = Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY)


def _ph_s(value: str) -> Hashrate:
    return Hashrate(Decimal(value), HashUnit.PH, TimeUnit.SECOND)


def _price(sats: int) -> HashratePrice:
    return HashratePrice(sats=Sats(sats), per=EH_DAY)


_ZERO_BALANCE = AccountBalance(
    available_sat=Sats(0), blocked_sat=Sats(0), total_sat=Sats(0)
)


def _config(target_ph_s: str = "10") -> TargetHashrateConfig:
    return TargetHashrateConfig(
        default_amount=Sats(100_000),
        upstream=UPSTREAM,
        target_hashrate=_ph_s(target_ph_s),
    )


def _inputs(
    *,
    target: Hashrate | None = None,
    needed: Hashrate | None = None,
    price: HashratePrice | None = None,
    bids_with_cooldowns: tuple[BidWithCooldown, ...] = (),
) -> TargetHashrateInputs:
    target_v = target if target is not None else _ph_s("10")
    needed_v = needed if needed is not None else _ph_s("45")
    price_v = price if price is not None else _price(501_000)
    return TargetHashrateInputs(
        ocean_24h=_ph_s("5"),
        target=target_v,
        needed_hashrate=needed_v,
        target_price=price_v,
        bids_with_cooldowns=bids_with_cooldowns,
        non_manageable_bids=(),
        available_balance=_ZERO_BALANCE,
    )


def _bwc(
    bid_id: str = "B1",
    price_sat_per_ph_day: int = 500,
    speed: str = "10",
    price_cd: bool = False,
    speed_cd: bool = False,
) -> BidWithCooldown:
    return BidWithCooldown(
        bid=make_user_bid(bid_id, price_sat_per_ph_day, speed),
        is_price_in_cooldown=price_cd,
        is_speed_in_cooldown=speed_cd,
    )


class TestNoExistingBids:
    """When there are no existing bids, plans only differ in their creates."""

    def test_no_op_plan_present(self) -> None:
        """The empty plan (do nothing) is always a candidate."""
        plans = craft_all_possible_plans(_inputs(), _config())
        assert any(
            p.cancels == () and p.edits == () and p.unchanged == () and p.creates == ()
            for p in plans
        )

    def test_one_create_per_documented_target_fraction(self) -> None:
        """Each fraction in {10..100}% of target produces a candidate create."""
        target = _ph_s("10")
        plans = craft_all_possible_plans(
            _inputs(target=target, needed=_ph_s("5")), _config("10")
        )
        for f in _NEW_BID_TARGET_FRACTIONS_PERCENT:
            expected = target * Fraction(f, 100)
            assert any(
                len(p.creates) == 1 and p.creates[0].config.speed_limit == expected
                for p in plans
            ), f"Missing create at {f}% of target"

    def test_no_creates_when_needed_is_zero(self) -> None:
        """When needed_hashrate is zero, the only create option is no-create."""
        plans = craft_all_possible_plans(_inputs(needed=_ph_s("0")), _config())
        assert all(p.creates == () for p in plans)

    def test_create_at_exact_gap_present(self) -> None:
        """A create sized to the exact remaining gap is also a candidate."""
        plans = craft_all_possible_plans(_inputs(needed=_ph_s("33")), _config())
        assert any(
            len(p.creates) == 1 and p.creates[0].config.speed_limit == _ph_s("33")
            for p in plans
        )

    def test_creates_use_market_price_default_amount_and_upstream(self) -> None:
        """Every create uses market price, default amount, configured upstream."""
        cfg = _config()
        market = _price(501_000)
        plans = craft_all_possible_plans(_inputs(price=market), cfg)
        for p in plans:
            for c in p.creates:
                assert c.config.price == market
                assert c.amount == cfg.default_amount
                assert c.upstream == cfg.upstream
                assert c.replaces is None


class TestSingleBid:
    """Per-bid action coverage with a single existing bid."""

    def test_cancel_plan_present(self) -> None:
        """Some candidate plan cancels the lone bid and creates nothing."""
        bwc = _bwc()
        plans = craft_all_possible_plans(_inputs(bids_with_cooldowns=(bwc,)), _config())
        assert any(
            len(p.cancels) == 1
            and p.cancels[0].bid is bwc.bid
            and p.creates == ()
            and p.edits == ()
            and p.unchanged == ()
            for p in plans
        )

    def test_unchanged_plan_present(self) -> None:
        """Some candidate plan keeps the bid as-is and creates nothing."""
        bwc = _bwc()
        plans = craft_all_possible_plans(_inputs(bids_with_cooldowns=(bwc,)), _config())
        assert any(
            p.unchanged == (bwc.bid,)
            and p.creates == ()
            and p.edits == ()
            and p.cancels == ()
            for p in plans
        )

    def test_each_speed_increase_percent_present(self) -> None:
        """Every documented speed increase percentage shows up as an edit."""
        bwc = _bwc(speed="10")
        plans = craft_all_possible_plans(_inputs(bids_with_cooldowns=(bwc,)), _config())
        current = bwc.bid.speed_limit_ph
        for p in _SPEED_INCREASE_PERCENTS:
            expected = current * Fraction(100 + p, 100)
            assert any(
                len(plan.edits) == 1 and plan.edits[0].new_speed_limit_ph == expected
                for plan in plans
            ), f"Missing +{p}% speed increase"

    def test_each_speed_decrease_percent_present_when_no_cooldown(self) -> None:
        """Every documented speed decrease percentage shows up when not locked."""
        bwc = _bwc(speed="10", speed_cd=False)
        plans = craft_all_possible_plans(_inputs(bids_with_cooldowns=(bwc,)), _config())
        current = bwc.bid.speed_limit_ph
        for p in _SPEED_DECREASE_PERCENTS:
            expected = current * Fraction(100 - p, 100)
            assert any(
                len(plan.edits) == 1 and plan.edits[0].new_speed_limit_ph == expected
                for plan in plans
            ), f"Missing -{p}% speed decrease"

    def test_no_speed_decrease_when_speed_cooldown(self) -> None:
        """A speed-locked bid never decreases its hashrate in any plan."""
        bwc = _bwc(speed="10", speed_cd=True)
        plans = craft_all_possible_plans(
            _inputs(needed=_ph_s("100"), bids_with_cooldowns=(bwc,)), _config()
        )
        current = bwc.bid.speed_limit_ph
        for plan in plans:
            for edit in plan.edits:
                assert edit.new_speed_limit_ph >= current

    def test_set_speed_to_exact_needed_value_present(self) -> None:
        """The gap-closing 'set hashrate to needed_total' option is enumerated."""
        bwc = _bwc(speed="10")
        plans = craft_all_possible_plans(
            _inputs(needed=_ph_s("33"), bids_with_cooldowns=(bwc,)), _config()
        )
        # 33 isn't a clean ±% of 10, so it can only come from "set to needed".
        assert any(
            len(plan.edits) == 1 and plan.edits[0].new_speed_limit_ph == _ph_s("33")
            for plan in plans
        )

    def test_increase_price_to_target_present_when_target_higher(self) -> None:
        """When target > bid price, raising the price to target is enumerated."""
        bwc = _bwc(price_sat_per_ph_day=400)
        market = _price(500_000)
        plans = craft_all_possible_plans(
            _inputs(price=market, bids_with_cooldowns=(bwc,)), _config()
        )
        assert any(edit.new_price == market for plan in plans for edit in plan.edits)

    def test_no_price_decrease_when_target_higher(self) -> None:
        """When target > bid price, no edit ever lowers the price."""
        bwc = _bwc(price_sat_per_ph_day=400)
        market = _price(500_000)
        plans = craft_all_possible_plans(
            _inputs(price=market, bids_with_cooldowns=(bwc,)), _config()
        )
        for plan in plans:
            for edit in plan.edits:
                assert edit.new_price >= bwc.bid.price

    def test_decrease_price_to_target_present_when_no_price_cooldown(self) -> None:
        """When target < bid price and price isn't locked, decrease is enumerated."""
        bwc = _bwc(price_sat_per_ph_day=600, price_cd=False)
        market = _price(500_000)
        plans = craft_all_possible_plans(
            _inputs(price=market, bids_with_cooldowns=(bwc,)), _config()
        )
        assert any(edit.new_price == market for plan in plans for edit in plan.edits)

    def test_no_price_decrease_when_in_price_cooldown(self) -> None:
        """A price-locked bid never decreases its price in any plan."""
        bwc = _bwc(price_sat_per_ph_day=600, price_cd=True)
        market = _price(500_000)
        plans = craft_all_possible_plans(
            _inputs(price=market, bids_with_cooldowns=(bwc,)), _config()
        )
        for plan in plans:
            for edit in plan.edits:
                assert edit.new_price >= bwc.bid.price

    def test_each_existing_bid_has_exactly_one_disposition(self) -> None:
        """A bid appears in exactly one of cancels, edits, unchanged per plan."""
        bwc = _bwc()
        plans = craft_all_possible_plans(_inputs(bids_with_cooldowns=(bwc,)), _config())
        for plan in plans:
            ids: list[str] = []
            ids += [c.bid.id for c in plan.cancels]
            ids += [e.bid.id for e in plan.edits]
            ids += [u.id for u in plan.unchanged]
            assert ids == [bwc.bid.id]

    def test_no_edit_is_a_no_op(self) -> None:
        """Edits must change at least one of price or speed."""
        bwc = _bwc()
        plans = craft_all_possible_plans(_inputs(bids_with_cooldowns=(bwc,)), _config())
        for plan in plans:
            for edit in plan.edits:
                assert edit.price_changed or edit.speed_limit_changed


class TestMultipleBids:
    """Cartesian per-bid combinations with two existing bids."""

    def test_each_bid_has_exactly_one_disposition_per_plan(self) -> None:
        """Every plan partitions the existing bids across cancels/edits/unchanged."""
        bwc1 = _bwc("B1", 500, "10")
        bwc2 = _bwc("B2", 600, "5")
        plans = craft_all_possible_plans(
            _inputs(bids_with_cooldowns=(bwc1, bwc2)), _config()
        )
        for plan in plans:
            ids: list[str] = []
            ids += [c.bid.id for c in plan.cancels]
            ids += [e.bid.id for e in plan.edits]
            ids += [u.id for u in plan.unchanged]
            assert sorted(ids) == ["B1", "B2"]

    def test_cancel_all_plan_present(self) -> None:
        """A plan that cancels every existing bid (no creates) is enumerated."""
        bwc1 = _bwc("B1", 500, "10")
        bwc2 = _bwc("B2", 600, "5")
        plans = craft_all_possible_plans(
            _inputs(bids_with_cooldowns=(bwc1, bwc2)), _config()
        )
        assert any(
            {c.bid.id for c in p.cancels} == {"B1", "B2"} and p.creates == ()
            for p in plans
        )

    def test_keep_all_unchanged_plan_present(self) -> None:
        """A plan that leaves every existing bid alone is enumerated."""
        bwc1 = _bwc("B1", 500, "10")
        bwc2 = _bwc("B2", 600, "5")
        plans = craft_all_possible_plans(
            _inputs(bids_with_cooldowns=(bwc1, bwc2)), _config()
        )
        assert any(
            {u.id for u in p.unchanged} == {"B1", "B2"} and p.creates == ()
            for p in plans
        )

    def test_mixed_disposition_plan_present(self) -> None:
        """Cancel one bid + keep the other should appear as a candidate."""
        bwc1 = _bwc("B1", 500, "10")
        bwc2 = _bwc("B2", 600, "5")
        plans = craft_all_possible_plans(
            _inputs(bids_with_cooldowns=(bwc1, bwc2)), _config()
        )
        assert any(
            len(p.cancels) == 1
            and p.cancels[0].bid.id == "B1"
            and p.unchanged == (bwc2.bid,)
            and p.creates == ()
            for p in plans
        )


class TestInvariants:
    """Whole-population invariants over the candidate set."""

    def _two_bid_inputs(
        self,
    ) -> tuple[TargetHashrateInputs, BidWithCooldown, BidWithCooldown]:
        bwc1 = _bwc("B1", 500, "10")
        bwc2 = _bwc("B2", 600, "5", price_cd=True, speed_cd=True)
        return (
            _inputs(bids_with_cooldowns=(bwc1, bwc2), price=_price(550_000)),
            bwc1,
            bwc2,
        )

    def test_locked_bid_never_decreases_price(self) -> None:
        """No candidate edit lowers the price of a price-locked bid."""
        inputs, _, locked = self._two_bid_inputs()
        plans = craft_all_possible_plans(inputs, _config())
        for plan in plans:
            for edit in plan.edits:
                if edit.bid.id == locked.bid.id:
                    assert edit.new_price >= locked.bid.price

    def test_locked_bid_never_decreases_speed(self) -> None:
        """No candidate edit lowers the speed of a speed-locked bid."""
        inputs, _, locked = self._two_bid_inputs()
        plans = craft_all_possible_plans(inputs, _config())
        for plan in plans:
            for edit in plan.edits:
                if edit.bid.id == locked.bid.id:
                    assert edit.new_speed_limit_ph >= locked.bid.speed_limit_ph

    def test_no_duplicate_plans(self) -> None:
        """Candidates are structurally unique."""
        inputs, _, _ = self._two_bid_inputs()
        plans = craft_all_possible_plans(inputs, _config())
        keys = [_plan_key(p) for p in plans]
        assert len(keys) == len(set(keys))

    def test_deterministic_across_runs(self) -> None:
        """Same inputs produce the same candidate sequence each time."""
        inputs, _, _ = self._two_bid_inputs()
        a = craft_all_possible_plans(inputs, _config())
        b = craft_all_possible_plans(inputs, _config())
        assert a == b

    def test_all_creates_use_market_price(self) -> None:
        """Every create's price is the market price from inputs."""
        inputs, _, _ = self._two_bid_inputs()
        plans = craft_all_possible_plans(inputs, _config())
        for plan in plans:
            for c in plan.creates:
                assert c.config.price == inputs.target_price

    def test_creates_speeds_in_documented_set(self) -> None:
        """Create speeds are 10..100% of target, or the exact remaining gap."""
        target = _ph_s("10")
        inputs = _inputs(target=target, needed=_ph_s("45"))
        plans = craft_all_possible_plans(inputs, _config("10"))
        valid: set[Hashrate] = {
            target * Fraction(f, 100) for f in _NEW_BID_TARGET_FRACTIONS_PERCENT
        }
        valid.add(inputs.needed_hashrate)
        for plan in plans:
            for c in plan.creates:
                assert c.config.speed_limit in valid

    def test_at_most_one_create_per_plan(self) -> None:
        """Per the documented action list, plans create at most one new bid."""
        inputs, _, _ = self._two_bid_inputs()
        plans = craft_all_possible_plans(inputs, _config())
        for plan in plans:
            assert len(plan.creates) <= 1

    def test_no_overlap_across_disposition_buckets(self) -> None:
        """Each existing bid id appears in at most one of cancels/edits/unchanged."""
        inputs, _, _ = self._two_bid_inputs()
        plans = craft_all_possible_plans(inputs, _config())
        for plan in plans:
            cancel_ids = {c.bid.id for c in plan.cancels}
            edit_ids = {e.bid.id for e in plan.edits}
            unchanged_ids = {u.id for u in plan.unchanged}
            assert cancel_ids.isdisjoint(edit_ids)
            assert cancel_ids.isdisjoint(unchanged_ids)
            assert edit_ids.isdisjoint(unchanged_ids)

    def test_no_edit_or_create_below_min_bid_speed_limit(self) -> None:
        """Speeds smaller than the bid floor (1 PH/s) are filtered out."""
        # Tiny bid (2 PH/s) where percentage decreases would dip below 1 PH/s,
        # plus a small target so fraction-of-target also lands sub-min.
        bwc = _bwc("B1", 500, "2", price_cd=False, speed_cd=False)
        plans = craft_all_possible_plans(
            _inputs(
                target=_ph_s("5"),
                needed=_ph_s("0.4"),
                bids_with_cooldowns=(bwc,),
            ),
            _config("5"),
        )
        for plan in plans:
            for edit in plan.edits:
                assert edit.new_speed_limit_ph >= MIN_BID_SPEED_LIMIT
            for create in plan.creates:
                assert create.config.speed_limit >= MIN_BID_SPEED_LIMIT


class TestCreateOptionsOrthogonal:
    """The create slot pairs independently with every per-bid combination."""

    def test_each_bid_combo_pairs_with_no_create_and_with_creates(self) -> None:
        """Both 'cancel + create' and 'unchanged + create' (etc.) appear."""
        bwc = _bwc()
        plans = craft_all_possible_plans(_inputs(bids_with_cooldowns=(bwc,)), _config())
        # Cancel + no-create
        assert any(len(p.cancels) == 1 and p.creates == () for p in plans)
        # Cancel + create
        assert any(len(p.cancels) == 1 and len(p.creates) == 1 for p in plans)
        # Unchanged + no-create
        assert any(p.unchanged == (bwc.bid,) and p.creates == () for p in plans)
        # Unchanged + create
        assert any(p.unchanged == (bwc.bid,) and len(p.creates) == 1 for p in plans)
