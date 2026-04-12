"""Tests for dry-run output formatting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hashbidder.bid_runner import SetBidsResult
from hashbidder.client import BidStatus
from hashbidder.domain.bid_planning import (
    CancelAction,
    CancelReason,
    CreateAction,
    EditAction,
    ReconciliationPlan,
    UnchangedBid,
)
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.formatting import (
    format_plan,
    format_set_bids_target_result_verbose,
)
from hashbidder.target_hashrate import BidWithCooldown, CooldownInfo
from hashbidder.use_cases.set_bids_target import (
    SetBidsTargetResult,
    TargetHashrateInputs,
)
from tests.conftest import PH_DAY, UPSTREAM, make_bid_config, make_user_bid


def _empty_plan() -> ReconciliationPlan:
    return ReconciliationPlan(edits=(), creates=(), cancels=(), unchanged=())


class TestFormatPlan:
    """Tests for format_plan."""

    def test_no_changes(self) -> None:
        """No changes prints 'No changes needed.'."""
        output = format_plan(_empty_plan(), ())

        assert "No changes needed." in output
        assert "=== Expected Final State ===" in output
        assert "No active bids." in output

    def test_edit_price_only(self) -> None:
        """Edit with only price change shows arrow for price, unchanged for rest."""
        bid = make_user_bid("B123", 400, "5.0")
        plan = ReconciliationPlan(
            edits=(
                EditAction(
                    bid=bid,
                    old_price=bid.price,
                    old_speed_limit_ph=bid.speed_limit_ph,
                    new_price=HashratePrice(sats=Sats(500), per=PH_DAY),
                    new_speed_limit_ph=bid.speed_limit_ph,
                ),
            ),
            creates=(),
            cancels=(),
            unchanged=(),
        )
        output = format_plan(plan, ())

        assert "EDIT B123:" in output
        assert "400 \u2192 500 sat/PH/Day" in output
        assert "5.0 PH/s (unchanged)" in output
        assert "upstream:    (unchanged)" in output
        # Final state.
        assert "EDITED, price 400\u2192500" in output

    def test_edit_speed_only(self) -> None:
        """Edit with only speed change shows arrow for speed, unchanged for price."""
        bid = make_user_bid("B456", 500, "3.0")
        plan = ReconciliationPlan(
            edits=(
                EditAction(
                    bid=bid,
                    old_price=bid.price,
                    old_speed_limit_ph=bid.speed_limit_ph,
                    new_price=bid.price,
                    new_speed_limit_ph=Hashrate(
                        Decimal("5.0"), HashUnit.PH, TimeUnit.SECOND
                    ),
                ),
            ),
            creates=(),
            cancels=(),
            unchanged=(),
        )
        output = format_plan(plan, ())

        assert "EDIT B456:" in output
        assert "500 sat/PH/Day (unchanged)" in output
        assert "3.0 \u2192 5.0 PH/s" in output
        assert "EDITED, speed_limit 3.0\u21925.0" in output

    def test_edit_both_fields(self) -> None:
        """Edit with both fields changed shows arrows for both."""
        bid = make_user_bid("B789", 400, "3.0")
        plan = ReconciliationPlan(
            edits=(
                EditAction(
                    bid=bid,
                    old_price=bid.price,
                    old_speed_limit_ph=bid.speed_limit_ph,
                    new_price=HashratePrice(sats=Sats(500), per=PH_DAY),
                    new_speed_limit_ph=Hashrate(
                        Decimal("5.0"), HashUnit.PH, TimeUnit.SECOND
                    ),
                ),
            ),
            creates=(),
            cancels=(),
            unchanged=(),
        )
        output = format_plan(plan, ())

        assert "400 \u2192 500 sat/PH/Day" in output
        assert "3.0 \u2192 5.0 PH/s" in output
        assert "EDITED, price 400\u2192500, speed_limit 3.0\u21925.0" in output

    def test_create(self) -> None:
        """Create shows all fields including amount and upstream."""
        cfg = make_bid_config(300, "10.0")
        plan = ReconciliationPlan(
            edits=(),
            creates=(
                CreateAction(
                    config=cfg,
                    amount=Sats(100_000),
                    upstream=UPSTREAM,
                ),
            ),
            cancels=(),
            unchanged=(),
        )
        output = format_plan(plan, ())

        assert "CREATE:" in output
        assert "300 sat/PH/Day" in output
        assert "10.0 PH/s" in output
        assert "100000 sat" in output
        assert "stratum+tcp://pool.example.com:3333 / worker1" in output
        assert "(NEW)" in output

    def test_cancel_unmatched(self) -> None:
        """Cancel with UNMATCHED reason shows the bid details and reason."""
        bid = make_user_bid("B987", 600, "3.0")
        plan = ReconciliationPlan(
            edits=(),
            creates=(),
            cancels=(CancelAction(bid=bid, reason=CancelReason.UNMATCHED),),
            unchanged=(),
        )
        output = format_plan(plan, ())

        assert "CANCEL B987:" in output
        assert "600 sat/PH/Day" in output
        assert "3.0 PH/s" in output
        assert "no matching config entry" in output
        # Canceled bid should NOT appear in final state.
        assert "B987" not in output.split("=== Expected Final State ===")[1]

    def test_cancel_upstream_mismatch_with_replacement(self) -> None:
        """Upstream mismatch cancel is followed by its replacement create."""
        bid = make_user_bid("B111", 500, "5.0")
        cfg = make_bid_config(500, "5.0")
        plan = ReconciliationPlan(
            edits=(),
            creates=(
                CreateAction(
                    config=cfg,
                    amount=Sats(100_000),
                    upstream=UPSTREAM,
                    replaces=bid,
                ),
            ),
            cancels=(CancelAction(bid=bid, reason=CancelReason.UPSTREAM_MISMATCH),),
            unchanged=(),
        )
        output = format_plan(plan, ())

        lines = output.split("\n")
        cancel_idx = next(i for i, line in enumerate(lines) if "CANCEL B111:" in line)
        # The replacement create should follow the cancel.
        remaining = "\n".join(lines[cancel_idx:])
        assert "upstream mismatch" in remaining
        assert "CREATE (replaces B111):" in remaining
        # Final state shows the new bid.
        assert "(NEW)" in output

    def test_unchanged_in_final_state(self) -> None:
        """Unchanged bids appear in the final state."""
        bid = make_user_bid("B222", 500, "5.0")
        plan = ReconciliationPlan(
            edits=(),
            creates=(),
            cancels=(),
            unchanged=(UnchangedBid(bid=bid),),
        )
        output = format_plan(plan, ())

        assert "No changes needed." in output
        assert "(UNCHANGED)" in output

    def test_skipped_bids_in_final_state(self) -> None:
        """Skipped (PAUSED/FROZEN) bids appear in the final state."""
        paused = make_user_bid("B333", 200, "3.0", status=BidStatus.PAUSED)
        output = format_plan(_empty_plan(), (paused,))

        final = output.split("=== Expected Final State ===")[1]
        assert "200 sat/PH/Day" in final
        assert "(PAUSED)" in final

    def test_mixed_scenario(self) -> None:
        """A mixed plan with edit, create, cancel, unchanged, and skipped."""
        bid_edit = make_user_bid("B1", 400, "5.0", amount=200_000)
        bid_cancel = make_user_bid("B2", 600, "3.0")
        bid_unchanged = make_user_bid("B3", 500, "10.0")
        bid_paused = make_user_bid("B4", 999, "99.0", status=BidStatus.PAUSED)

        cfg_new = make_bid_config(700, "20.0")

        plan = ReconciliationPlan(
            edits=(
                EditAction(
                    bid=bid_edit,
                    old_price=bid_edit.price,
                    old_speed_limit_ph=bid_edit.speed_limit_ph,
                    new_price=HashratePrice(sats=Sats(500), per=PH_DAY),
                    new_speed_limit_ph=bid_edit.speed_limit_ph,
                ),
            ),
            creates=(
                CreateAction(
                    config=cfg_new,
                    amount=Sats(100_000),
                    upstream=UPSTREAM,
                ),
            ),
            cancels=(CancelAction(bid=bid_cancel, reason=CancelReason.UNMATCHED),),
            unchanged=(UnchangedBid(bid=bid_unchanged),),
        )
        output = format_plan(plan, (bid_paused,))

        assert "=== Changes ===" in output
        assert "EDIT B1:" in output
        assert "CANCEL B2:" in output
        assert "CREATE:" in output
        assert "=== Expected Final State ===" in output
        # All surviving bids in final state.
        assert "(EDITED" in output
        assert "(NEW)" in output
        assert "(UNCHANGED)" in output
        assert "(PAUSED)" in output

    def test_only_cancels(self) -> None:
        """Plan with only cancels shows empty final state."""
        bid = make_user_bid("B1", 500, "5.0")
        plan = ReconciliationPlan(
            edits=(),
            creates=(),
            cancels=(CancelAction(bid=bid, reason=CancelReason.UNMATCHED),),
            unchanged=(),
        )
        output = format_plan(plan, ())

        assert "CANCEL B1:" in output
        assert "No active bids." in output


class TestFormatTargetHashrateVerbose:
    """Tests for format_set_bids_target_result_verbose."""

    def _result(self, annotated: tuple[BidWithCooldown, ...]) -> SetBidsTargetResult:
        def ph_s(v: str) -> Hashrate:
            return Hashrate(Decimal(v), HashUnit.PH, TimeUnit.SECOND)

        inputs = TargetHashrateInputs(
            ocean_24h=ph_s("5"),
            target=ph_s("10"),
            needed=ph_s("15"),
            price=HashratePrice(sats=Sats(801), per=PH_DAY),
            max_bids_count=3,
            annotated_bids=annotated,
        )
        plan = ReconciliationPlan(edits=(), creates=(), cancels=(), unchanged=())
        return SetBidsTargetResult(
            inputs=inputs,
            set_bids_result=SetBidsResult(plan=plan, skipped_bids=(), execution=None),
        )

    def test_no_existing_bids(self) -> None:
        """No existing bids → cooldown section says so."""
        output = format_set_bids_target_result_verbose(self._result(()))
        assert "=== Reasoning ===" in output
        assert "Price scan:   lowest served bid 800 sat/PH/Day" in output
        assert "→ undercut by 1 sat → 801 sat/PH/Day" in output
        assert "Needed math:  2 * 10.0 (target) - 5.0 (ocean 24h) = 15.0 PH/s" in (
            output
        )
        assert "Slot budget:  up to 3 bids" in output
        assert "=== Cooldown Status ===" in output
        assert "(no existing bids)" in output

    def test_mixed_cooldowns(self) -> None:
        """Each cooldown combination renders a distinct status label."""
        now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=UTC)
        b_free = make_user_bid("B1", 700, "1.0", last_updated=now - timedelta(days=1))
        b_price = make_user_bid("B2", 800, "2.0", last_updated=now)
        b_speed = make_user_bid("B3", 900, "3.0", last_updated=now)
        b_both = make_user_bid("B4", 950, "4.0", last_updated=now)
        annotated = (
            BidWithCooldown(
                bid=b_free,
                cooldown=CooldownInfo(price_cooldown=False, speed_cooldown=False),
            ),
            BidWithCooldown(
                bid=b_price,
                cooldown=CooldownInfo(price_cooldown=True, speed_cooldown=False),
            ),
            BidWithCooldown(
                bid=b_speed,
                cooldown=CooldownInfo(price_cooldown=False, speed_cooldown=True),
            ),
            BidWithCooldown(
                bid=b_both,
                cooldown=CooldownInfo(price_cooldown=True, speed_cooldown=True),
            ),
        )
        output = format_set_bids_target_result_verbose(self._result(annotated))
        assert "B1  price=700 sat/PH/Day  limit=1 PH/Second  → free" in output
        assert "B2  price=800 sat/PH/Day  limit=2 PH/Second  → price locked" in output
        assert "B3" in output
        assert "→ speed locked (price free)" in output
        assert "B4  price=950 sat/PH/Day  limit=4 PH/Second  → price+speed" in output
