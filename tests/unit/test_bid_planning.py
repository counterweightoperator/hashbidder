"""Tests for the pure bid planner."""

from decimal import Decimal

import pytest
from hypothesis import given, settings, strategies
from hypothesis.strategies import DrawFn, composite

from hashbidder.clients.braiins import BidStatus, UserBid
from hashbidder.domain.bid_config import (
    MIN_BID_SPEED_LIMIT,
    BidConfig,
    SetBidsConfig,
)
from hashbidder.domain.bid_planning import CancelReason, EditAction, plan_bid_changes
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from tests.conftest import (
    OTHER_UPSTREAM,
    PH_DAY,
    UPSTREAM,
    make_bid_config,
    make_config,
    make_user_bid,
)


class TestReconcile:
    """Tests for the reconcile function."""

    def test_empty_config_empty_bids(self) -> None:
        """No config entries and no bids produces an empty plan."""
        plan = plan_bid_changes(make_config(), ())

        assert plan.edits == ()
        assert plan.creates == ()
        assert plan.cancels == ()
        assert plan.unchanged == ()

    def test_exact_match_is_unchanged(self) -> None:
        """A bid that matches config exactly is unchanged."""
        bid = make_user_bid("B1", 500, "5.0")
        plan = plan_bid_changes(make_config(make_bid_config(500, "5.0")), (bid,))

        assert len(plan.unchanged) == 1
        assert plan.unchanged[0] is bid
        assert plan.edits == ()
        assert plan.creates == ()
        assert plan.cancels == ()

    def test_config_extra_entries_become_creates(self) -> None:
        """Config entries with no matching bid become creates."""
        c1 = make_bid_config(500, "5.0")
        c2 = make_bid_config(300, "10.0")
        plan = plan_bid_changes(make_config(c1, c2), ())

        assert len(plan.creates) == 2
        assert plan.creates[0].config is c1
        assert plan.creates[0].amount == Sats(100_000)
        assert plan.creates[1].config is c2
        assert plan.creates[0].replaces is None

    def test_existing_bids_not_in_config_become_cancels(self) -> None:
        """Existing bids with no matching config entry become cancels."""
        bid = make_user_bid("B1", 500, "5.0")
        plan = plan_bid_changes(make_config(), (bid,))

        assert len(plan.cancels) == 1
        assert plan.cancels[0].bid is bid
        assert plan.cancels[0].reason == CancelReason.UNMATCHED

    def test_price_differs_becomes_edit(self) -> None:
        """A bid where only price differs becomes an edit."""
        bid = make_user_bid("B1", 400, "5.0")
        plan = plan_bid_changes(make_config(make_bid_config(500, "5.0")), (bid,))

        assert len(plan.edits) == 1
        edit = plan.edits[0]
        assert edit.bid is bid
        assert edit.price_changed
        assert not edit.speed_limit_changed
        assert edit.new_price.sats == Sats(500)

    def test_speed_limit_differs_becomes_edit(self) -> None:
        """A bid where only speed limit differs becomes an edit."""
        bid = make_user_bid("B1", 500, "3.0")
        plan = plan_bid_changes(make_config(make_bid_config(500, "5.0")), (bid,))

        assert len(plan.edits) == 1
        edit = plan.edits[0]
        assert not edit.price_changed
        assert edit.speed_limit_changed

    def test_both_fields_differ_becomes_edit(self) -> None:
        """A bid where both price and speed limit differ becomes an edit."""
        bid = make_user_bid("B1", 400, "3.0")
        plan = plan_bid_changes(make_config(make_bid_config(500, "5.0")), (bid,))

        assert len(plan.edits) == 1
        edit = plan.edits[0]
        assert edit.price_changed
        assert edit.speed_limit_changed

    def test_upstream_mismatch_becomes_cancel_and_create(self) -> None:
        """A bid with mismatched upstream is canceled and recreated."""
        bid = make_user_bid("B1", 500, "5.0", upstream=OTHER_UPSTREAM)
        cfg = make_bid_config(500, "5.0")
        plan = plan_bid_changes(make_config(cfg), (bid,))

        assert len(plan.cancels) == 1
        assert plan.cancels[0].bid is bid
        assert plan.cancels[0].reason == CancelReason.UPSTREAM_MISMATCH
        assert len(plan.creates) == 1
        assert plan.creates[0].config is cfg
        assert plan.creates[0].replaces is bid
        assert plan.edits == ()
        assert plan.unchanged == ()

    def test_greedy_ordering_highest_remaining_first(self) -> None:
        """Highest-remaining bid gets first pick when bids compete for a config entry.

        Both bids are equally close to the single config entry (1 diff each),
        but bid_high has more remaining amount so it should win the match,
        leaving bid_low to be canceled.
        """
        bid_high = make_user_bid("B1", 500, "3.0", remaining=200_000)  # 1 diff (speed)
        bid_low = make_user_bid("B2", 400, "5.0", remaining=10_000)  # 1 diff (price)

        cfg = make_bid_config(500, "5.0")

        plan = plan_bid_changes(
            make_config(cfg), (bid_low, bid_high)
        )  # pass in wrong order

        # bid_high should get the match (edit), bid_low should be canceled.
        assert len(plan.edits) == 1
        assert plan.edits[0].bid is bid_high
        assert plan.edits[0].speed_limit_changed
        assert not plan.edits[0].price_changed

        assert len(plan.cancels) == 1
        assert plan.cancels[0].bid is bid_low

    def test_fewest_changes_wins(self) -> None:
        """When multiple config entries could match, the one with fewest diffs wins."""
        bid = make_user_bid("B1", 500, "5.0")

        c_exact = make_bid_config(500, "5.0")  # 0 diffs
        c_one_off = make_bid_config(600, "5.0")  # 1 diff

        plan = plan_bid_changes(make_config(c_exact, c_one_off), (bid,))

        assert len(plan.unchanged) == 1
        assert len(plan.creates) == 1
        assert plan.creates[0].config is c_one_off

    def test_paused_bids_skipped(self) -> None:
        """PAUSED bids are not matched, edited, or canceled."""
        bid = make_user_bid("B1", 500, "5.0", status=BidStatus.PAUSED)
        plan = plan_bid_changes(make_config(make_bid_config(500, "5.0")), (bid,))

        # The PAUSED bid is invisible to reconciliation — config entry becomes a create.
        assert plan.unchanged == ()
        assert plan.cancels == ()
        assert plan.edits == ()
        assert len(plan.creates) == 1

    def test_frozen_bids_skipped(self) -> None:
        """FROZEN bids are not matched, edited, or canceled."""
        bid = make_user_bid("B1", 500, "5.0", status=BidStatus.FROZEN)
        plan = plan_bid_changes(make_config(make_bid_config(500, "5.0")), (bid,))

        assert plan.unchanged == ()
        assert plan.cancels == ()
        assert len(plan.creates) == 1

    def test_canceled_bids_skipped(self) -> None:
        """Already-canceled bids are ignored."""
        bid = make_user_bid("B1", 500, "5.0", status=BidStatus.CANCELED)
        plan = plan_bid_changes(make_config(make_bid_config(500, "5.0")), (bid,))

        assert len(plan.creates) == 1
        assert plan.cancels == ()

    def test_mixed_scenario(self) -> None:
        """A realistic scenario with edits, creates, cancels, and unchanged."""
        bid_unchanged = make_user_bid("B1", 500, "5.0", remaining=300_000)
        bid_edit = make_user_bid("B2", 400, "10.0", remaining=200_000)
        bid_cancel = make_user_bid("B3", 100, "1.0", remaining=50_000)
        bid_paused = make_user_bid("B4", 999, "99.0", status=BidStatus.PAUSED)

        # 2 config entries for 3 active bids → 1 cancel.
        # Plus 1 new config entry → 1 create.
        c_unchanged = make_bid_config(500, "5.0")
        c_edit = make_bid_config(300, "10.0")

        plan = plan_bid_changes(
            make_config(c_unchanged, c_edit),
            (bid_unchanged, bid_edit, bid_cancel, bid_paused),
        )

        assert len(plan.unchanged) == 1
        assert plan.unchanged[0] is bid_unchanged

        assert len(plan.edits) == 1
        assert plan.edits[0].bid is bid_edit
        assert plan.edits[0].price_changed
        assert not plan.edits[0].speed_limit_changed

        assert len(plan.cancels) == 1
        assert plan.cancels[0].bid is bid_cancel
        assert plan.cancels[0].reason == CancelReason.UNMATCHED

        assert plan.creates == ()

    def test_empty_config_cancels_all_active(self) -> None:
        """Empty config with active bids cancels all of them."""
        bids = (
            make_user_bid("B1", 500, "5.0"),
            make_user_bid("B2", 300, "10.0"),
        )
        plan = plan_bid_changes(make_config(), bids)

        assert len(plan.cancels) == 2
        assert plan.edits == ()
        assert plan.creates == ()
        assert plan.unchanged == ()

    def test_upstream_mismatch_on_edit_candidate(self) -> None:
        """A bid that would be an edit but has wrong upstream gets cancel+create."""
        bid = make_user_bid("B1", 400, "5.0", upstream=OTHER_UPSTREAM)
        cfg = make_bid_config(500, "5.0")
        plan = plan_bid_changes(make_config(cfg), (bid,))

        assert len(plan.cancels) == 1
        assert plan.cancels[0].reason == CancelReason.UPSTREAM_MISMATCH
        assert len(plan.creates) == 1
        assert plan.creates[0].replaces is bid
        assert plan.creates[0].config is cfg

    def test_all_paused_frozen_with_config_entries(self) -> None:
        """All bids are PAUSED/FROZEN — config entries become creates, no cancels."""
        bids = (
            make_user_bid("B1", 500, "5.0", status=BidStatus.PAUSED),
            make_user_bid("B2", 300, "10.0", status=BidStatus.FROZEN),
        )
        c1 = make_bid_config(500, "5.0")
        c2 = make_bid_config(300, "10.0")
        plan = plan_bid_changes(make_config(c1, c2), bids)

        assert len(plan.creates) == 2
        assert plan.cancels == ()
        assert plan.edits == ()
        assert plan.unchanged == ()


_MANAGEABLE = frozenset({BidStatus.ACTIVE, BidStatus.CREATED})
_ALL_STATUSES = list(BidStatus)

_price_int = strategies.integers(min_value=1, max_value=10_000)
_speed_str = strategies.sampled_from(["1.0", "5.0", "10.0", "20.0", "50.0"])
_remaining = strategies.integers(min_value=1, max_value=1_000_000)
_status = strategies.sampled_from(_ALL_STATUSES)
_upstream_choice = strategies.sampled_from([UPSTREAM, OTHER_UPSTREAM])


@composite
def _gen_bid_config(draw: DrawFn) -> BidConfig:
    return make_bid_config(draw(_price_int), draw(_speed_str))


@composite
def _gen_user_bid_with_id(draw: DrawFn, bid_id: str) -> UserBid:
    return make_user_bid(
        bid_id,
        draw(_price_int),
        draw(_speed_str),
        status=draw(_status),
        remaining=draw(_remaining),
        upstream=draw(_upstream_choice),
    )


@composite
def _gen_reconcile_inputs(
    draw: DrawFn,
) -> tuple[SetBidsConfig, tuple[UserBid, ...]]:
    n_configs = draw(strategies.integers(min_value=0, max_value=6))
    n_bids = draw(strategies.integers(min_value=0, max_value=6))
    config_entries = tuple(draw(_gen_bid_config()) for _ in range(n_configs))
    # Ensure unique bid IDs so set-based invariant checks are valid.
    bids = tuple(draw(_gen_user_bid_with_id(f"B{i}")) for i in range(n_bids))
    upstream = draw(_upstream_choice)
    cfg = SetBidsConfig(
        default_amount=Sats(100_000), upstream=upstream, bids=config_entries
    )
    return cfg, bids


class TestReconcileProperties:
    """Property-based tests for reconcile invariants."""

    @given(inputs=_gen_reconcile_inputs())
    @settings(max_examples=200)
    def test_every_manageable_bid_in_exactly_one_bucket(
        self, inputs: tuple[SetBidsConfig, tuple[UserBid, ...]]
    ) -> None:
        """Each ACTIVE/CREATED bid is in exactly one of: edit, unchanged, cancel."""
        cfg, bids = inputs
        plan = plan_bid_changes(cfg, bids)

        edited_ids = {e.bid.id for e in plan.edits}
        unchanged_ids = {u.id for u in plan.unchanged}
        canceled_ids = {c.bid.id for c in plan.cancels}

        manageable_ids = [b.id for b in bids if b.status in _MANAGEABLE]

        all_plan_ids = edited_ids | unchanged_ids | canceled_ids
        assert set(manageable_ids) == all_plan_ids
        # No overlaps.
        assert len(edited_ids) + len(unchanged_ids) + len(canceled_ids) == len(
            manageable_ids
        )

    @given(inputs=_gen_reconcile_inputs())
    @settings(max_examples=200)
    def test_non_manageable_bids_never_in_plan(
        self, inputs: tuple[SetBidsConfig, tuple[UserBid, ...]]
    ) -> None:
        """PAUSED, FROZEN, and other non-manageable bids never appear in the plan."""
        cfg, bids = inputs
        plan = plan_bid_changes(cfg, bids)

        non_manageable_ids = {b.id for b in bids if b.status not in _MANAGEABLE}

        plan_ids = (
            {e.bid.id for e in plan.edits}
            | {u.id for u in plan.unchanged}
            | {c.bid.id for c in plan.cancels}
        )
        assert non_manageable_ids.isdisjoint(plan_ids)

    @given(inputs=_gen_reconcile_inputs())
    @settings(max_examples=200)
    def test_config_entries_fully_accounted_for(
        self, inputs: tuple[SetBidsConfig, tuple[UserBid, ...]]
    ) -> None:
        """Each config entry is either matched (edit/unchanged) or becomes a create."""
        cfg, bids = inputs
        plan = plan_bid_changes(cfg, bids)

        matched_count = len(plan.edits) + len(plan.unchanged)
        # Upstream-mismatch cancels consume a config entry and produce a create.
        upstream_mismatch_count = sum(
            1 for c in plan.cancels if c.reason == CancelReason.UPSTREAM_MISMATCH
        )
        matched_count += upstream_mismatch_count
        pure_creates = len(plan.creates) - upstream_mismatch_count

        assert matched_count + pure_creates == len(cfg.bids)

    @given(inputs=_gen_reconcile_inputs())
    @settings(max_examples=200)
    def test_upstream_mismatch_cancels_have_replacement_creates(
        self, inputs: tuple[SetBidsConfig, tuple[UserBid, ...]]
    ) -> None:
        """Every upstream-mismatch cancel has a create with replaces set to that bid."""
        cfg, bids = inputs
        plan = plan_bid_changes(cfg, bids)

        mismatch_bids = {
            c.bid.id for c in plan.cancels if c.reason == CancelReason.UPSTREAM_MISMATCH
        }
        replacement_bids = {
            cr.replaces.id for cr in plan.creates if cr.replaces is not None
        }
        assert mismatch_bids == replacement_bids

    @given(inputs=_gen_reconcile_inputs())
    @settings(max_examples=200)
    def test_creates_use_default_amount(
        self, inputs: tuple[SetBidsConfig, tuple[UserBid, ...]]
    ) -> None:
        """All creates use the config's default_amount."""
        cfg, bids = inputs
        plan = plan_bid_changes(cfg, bids)

        for create in plan.creates:
            assert create.amount == cfg.default_amount


def _ph_s(value: str) -> Hashrate:
    return Hashrate(Decimal(value), HashUnit.PH, TimeUnit.SECOND)


class TestMinBidSpeedLimit:
    """Domain-level enforcement of the 1 PH/s minimum bid speed limit."""

    def test_min_bid_speed_limit_is_one_ph_per_second(self) -> None:
        """The constant matches the documented upstream-API floor."""
        assert _ph_s("1") == MIN_BID_SPEED_LIMIT

    def test_bid_config_at_min_is_accepted(self) -> None:
        """A speed_limit exactly equal to the floor is valid."""
        BidConfig(
            price=HashratePrice(sats=Sats(1_000), per=PH_DAY),
            speed_limit=MIN_BID_SPEED_LIMIT,
        )

    def test_bid_config_below_min_is_rejected(self) -> None:
        """Constructing a BidConfig below the floor raises ValueError."""
        with pytest.raises(ValueError, match="speed_limit must be >="):
            BidConfig(
                price=HashratePrice(sats=Sats(1_000), per=PH_DAY),
                speed_limit=_ph_s("0.5"),
            )

    def test_edit_action_at_min_is_accepted(self) -> None:
        """An EditAction setting speed exactly to the floor is valid."""
        bid = make_user_bid("B1", 500, "5.0")
        EditAction(bid=bid, new_price=bid.price, new_speed_limit_ph=MIN_BID_SPEED_LIMIT)

    def test_edit_action_below_min_is_rejected(self) -> None:
        """Constructing an EditAction below the floor raises ValueError."""
        bid = make_user_bid("B1", 500, "5.0")
        with pytest.raises(ValueError, match="new_speed_limit_ph must be >="):
            EditAction(bid=bid, new_price=bid.price, new_speed_limit_ph=_ph_s("0.5"))
