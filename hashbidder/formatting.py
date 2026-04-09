"""Output formatting for reconciliation plans and execution results."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from hashbidder.client import UserBid
from hashbidder.domain.hashrate import HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.reconcile import (
    CancelAction,
    CancelReason,
    CreateAction,
    EditAction,
    ReconciliationPlan,
)

if TYPE_CHECKING:
    from hashbidder.use_cases import ActionOutcome


def _fmt_speed(value: Decimal) -> str:
    """Format a speed limit value, keeping at least one decimal place."""
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return f"{normalized:.1f}"
    return str(normalized)


def _to_ph_day(price: HashratePrice) -> Sats:
    """Convert a hashrate price to sat/PH/Day."""
    return price.to(HashUnit.PH, TimeUnit.DAY).sats


def _format_edit(edit: EditAction) -> str:
    old_price = _to_ph_day(edit.old_price)
    new_price = _to_ph_day(edit.new_price)

    if edit.price_changed:
        price_line = f"  price:       {old_price} \u2192 {new_price} sat/PH/Day"
    else:
        price_line = f"  price:       {old_price} sat/PH/Day (unchanged)"

    if edit.speed_limit_changed:
        old_speed = _fmt_speed(edit.old_speed_limit_ph.value)
        new_speed = _fmt_speed(edit.new_speed_limit_ph.value)
        speed_line = f"  speed_limit: {old_speed} \u2192 {new_speed} PH/s"
    else:
        speed_line = (
            f"  speed_limit: "
            f"{_fmt_speed(edit.old_speed_limit_ph.value)} PH/s (unchanged)"
        )

    upstream_line = "  upstream:    (unchanged)"

    lines = [f"EDIT {edit.bid.id}:", price_line, speed_line, upstream_line]
    return "\n".join(lines)


def _format_create(create: CreateAction) -> str:
    price = _to_ph_day(create.config.price)
    speed = _fmt_speed(create.config.speed_limit.value)

    if create.replaces is not None:
        header = f"CREATE (replaces {create.replaces.id}):"
    else:
        header = "CREATE:"

    lines = [
        header,
        f"  price:       {price} sat/PH/Day",
        f"  speed_limit: {speed} PH/s",
        f"  amount:      {create.amount} sat",
        f"  upstream:    {create.upstream.url} / {create.upstream.identity}",
    ]
    return "\n".join(lines)


def _format_cancel(cancel: CancelAction) -> str:
    price = _to_ph_day(cancel.bid.price)
    speed = _fmt_speed(cancel.bid.speed_limit_ph.value)

    lines = [
        f"CANCEL {cancel.bid.id}:",
        f"  price:       {price} sat/PH/Day",
        f"  speed_limit: {speed} PH/s",
        f"  reason:      {cancel.reason.value}",
    ]
    return "\n".join(lines)


def _format_final_state_line(
    price_ph_day: Sats,
    speed: str,
    amount: Sats,
    annotation: str,
) -> str:
    return (
        f"BID  price={price_ph_day} sat/PH/Day  "
        f"limit={speed} PH/s  "
        f"amount={amount} sat  "
        f"({annotation})"
    )


def format_plan(plan: ReconciliationPlan, skipped_bids: tuple[UserBid, ...]) -> str:
    """Render a reconciliation plan as human-readable dry-run output.

    Args:
        plan: The reconciliation plan to format.
        skipped_bids: PAUSED/FROZEN bids to include in the final state.

    Returns:
        The formatted output string.
    """
    sections: list[str] = []

    has_changes = plan.edits or plan.creates or plan.cancels

    if not has_changes:
        sections.append("No changes needed.")
    else:
        sections.append("=== Changes ===")
        # Group upstream-mismatch cancels with their replacement creates.
        replacement_creates = {
            cr.replaces.id: cr for cr in plan.creates if cr.replaces is not None
        }

        for edit in plan.edits:
            sections.append(_format_edit(edit))

        for cancel in plan.cancels:
            sections.append(_format_cancel(cancel))
            if cancel.reason == CancelReason.UPSTREAM_MISMATCH:
                create = replacement_creates[cancel.bid.id]
                sections.append(_format_create(create))

        # Pure creates (not replacements).
        for create in plan.creates:
            if create.replaces is None:
                sections.append(_format_create(create))

    # Final expected state.
    state_lines: list[str] = []

    for edit in plan.edits:
        price = _to_ph_day(edit.new_price)
        speed = _fmt_speed(edit.new_speed_limit_ph.value)
        changes: list[str] = []
        if edit.price_changed:
            old = _to_ph_day(edit.old_price)
            changes.append(f"price {old}\u2192{price}")
        if edit.speed_limit_changed:
            old_s = _fmt_speed(edit.old_speed_limit_ph.value)
            new_s = _fmt_speed(edit.new_speed_limit_ph.value)
            changes.append(f"speed_limit {old_s}\u2192{new_s}")
        annotation = "EDITED, " + ", ".join(changes)
        state_lines.append(
            _format_final_state_line(price, speed, edit.bid.amount_sat, annotation)
        )

    for create in plan.creates:
        price = _to_ph_day(create.config.price)
        speed = _fmt_speed(create.config.speed_limit.value)
        state_lines.append(_format_final_state_line(price, speed, create.amount, "NEW"))

    for unch in plan.unchanged:
        price = _to_ph_day(unch.bid.price)
        speed = _fmt_speed(unch.bid.speed_limit_ph.value)
        state_lines.append(
            _format_final_state_line(price, speed, unch.bid.amount_sat, "UNCHANGED")
        )

    for bid in skipped_bids:
        price = _to_ph_day(bid.price)
        speed = _fmt_speed(bid.speed_limit_ph.value)
        state_lines.append(
            _format_final_state_line(price, speed, bid.amount_sat, bid.status.name)
        )

    sections.append("")
    sections.append("=== Expected Final State ===")
    if state_lines:
        sections.extend(state_lines)
    else:
        sections.append("No active bids.")

    return "\n".join(sections)


def format_create_label(create: CreateAction) -> str:
    """Format the label for a create action (used in execution output)."""
    price = _to_ph_day(create.config.price)
    speed = _fmt_speed(create.config.speed_limit.value)
    return f"CREATE {price} sat/PH/Day {speed} PH/s"


def format_outcome(outcome: ActionOutcome) -> str:
    """Format a single action outcome for real-time execution output."""
    if outcome.status.value == "succeeded":
        suffix = "OK"
        if outcome.created_id:
            suffix = f"OK \u2192 {outcome.created_id}"
        return f"{outcome.label}... {suffix}"
    if outcome.status.value == "failed":
        error_part = f": {outcome.error}" if outcome.error else ""
        return f"{outcome.label}... FAILED{error_part}"
    # skipped
    return "  skipping linked CREATE (upstream mismatch pair)"


def format_results_summary(outcomes: tuple[ActionOutcome, ...]) -> str:
    """Format the results summary line."""
    from hashbidder.use_cases import ActionStatus

    succeeded = sum(1 for o in outcomes if o.status == ActionStatus.SUCCEEDED)
    failed = sum(1 for o in outcomes if o.status == ActionStatus.FAILED)
    skipped = sum(1 for o in outcomes if o.status == ActionStatus.SKIPPED)
    parts = [f"{succeeded} succeeded", f"{failed} failed"]
    if skipped:
        parts.append(f"{skipped} skipped")
    return ", ".join(parts)


def format_current_bids(bids: tuple[UserBid, ...]) -> str:
    """Format the current bids section after execution."""
    if not bids:
        return "No active bids."
    lines = []
    for bid in bids:
        price_ph_day = _to_ph_day(bid.price)
        speed = _fmt_speed(bid.speed_limit_ph.value)
        lines.append(
            f"{bid.id}  price={price_ph_day} sat/PH/Day  "
            f"limit={speed} PH/s  "
            f"amount={bid.amount_sat} sat  "
            f"{bid.status.name}"
        )
    return "\n".join(lines)
