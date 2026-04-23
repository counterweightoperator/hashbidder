"""Formatters for reconciliation plans, execution outcomes, and balance checks."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from hashbidder.cli.formatting._common import fmt_speed, to_ph_day
from hashbidder.clients.braiins import UserBid
from hashbidder.domain.balance_check import (
    LOW_BALANCE_RUNWAY,
    BalanceCheck,
    BalanceStatus,
)
from hashbidder.domain.bid_planning import (
    CancelAction,
    CancelReason,
    CreateAction,
    EditAction,
    ReconciliationPlan,
)
from hashbidder.domain.sats import Sats
from hashbidder.services.bid_runner import ActionOutcome, ActionStatus, SetBidsResult


def _format_edit(edit: EditAction) -> str:
    old_price = to_ph_day(edit.bid.price)
    new_price = to_ph_day(edit.new_price)

    if edit.price_changed:
        price_line = f"  price:       {old_price} → {new_price} sat/PH/Day"
    else:
        price_line = f"  price:       {old_price} sat/PH/Day (unchanged)"

    if edit.speed_limit_changed:
        old_speed = fmt_speed(edit.bid.speed_limit_ph.value)
        new_speed = fmt_speed(edit.new_speed_limit_ph.value)
        speed_line = f"  speed_limit: {old_speed} → {new_speed} PH/s"
    else:
        speed_line = (
            f"  speed_limit: "
            f"{fmt_speed(edit.bid.speed_limit_ph.value)} PH/s (unchanged)"
        )

    upstream_line = "  upstream:    (unchanged)"

    lines = [f"EDIT {edit.bid.id}:", price_line, speed_line, upstream_line]
    return "\n".join(lines)


def _format_create(create: CreateAction) -> str:
    price = to_ph_day(create.config.price)
    speed = fmt_speed(create.config.speed_limit.value)

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
    price = to_ph_day(cancel.bid.price)
    speed = fmt_speed(cancel.bid.speed_limit_ph.value)

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
        price = to_ph_day(edit.new_price)
        speed = fmt_speed(edit.new_speed_limit_ph.value)
        changes: list[str] = []
        if edit.price_changed:
            old = to_ph_day(edit.bid.price)
            changes.append(f"price {old}→{price}")
        if edit.speed_limit_changed:
            old_s = fmt_speed(edit.bid.speed_limit_ph.value)
            new_s = fmt_speed(edit.new_speed_limit_ph.value)
            changes.append(f"speed_limit {old_s}→{new_s}")
        annotation = "EDITED, " + ", ".join(changes)
        state_lines.append(
            _format_final_state_line(price, speed, edit.bid.amount_sat, annotation)
        )

    for create in plan.creates:
        price = to_ph_day(create.config.price)
        speed = fmt_speed(create.config.speed_limit.value)
        state_lines.append(_format_final_state_line(price, speed, create.amount, "NEW"))

    for bid in plan.unchanged:
        price = to_ph_day(bid.price)
        speed = fmt_speed(bid.speed_limit_ph.value)
        state_lines.append(
            _format_final_state_line(price, speed, bid.amount_sat, "UNCHANGED")
        )

    for bid in skipped_bids:
        price = to_ph_day(bid.price)
        speed = fmt_speed(bid.speed_limit_ph.value)
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


def _action_label(action: CancelAction | EditAction | CreateAction) -> str:
    """Build a human-readable label for an action."""
    if isinstance(action, CancelAction):
        return f"CANCEL {action.bid.id}"
    if isinstance(action, EditAction):
        return f"EDIT {action.bid.id}"
    price = to_ph_day(action.config.price)
    speed = fmt_speed(action.config.speed_limit.value)
    return f"CREATE {price} sat/PH/Day {speed} PH/s"


def format_outcome(outcome: ActionOutcome) -> str:
    """Format a single action outcome for real-time execution output."""
    label = _action_label(outcome.action)
    if outcome.status == ActionStatus.SUCCEEDED:
        suffix = "OK"
        if outcome.created_id:
            suffix = f"OK → {outcome.created_id}"
        return f"{label}... {suffix}"
    if outcome.status == ActionStatus.FAILED:
        error_part = f": {outcome.error}" if outcome.error else ""
        attempt_part = ""
        if outcome.attempt is not None and outcome.max_attempts is not None:
            attempt_part = f" (attempt {outcome.attempt}/{outcome.max_attempts}"
            # If this is not the last attempt, indicate retry.
            if outcome.attempt < outcome.max_attempts:
                attempt_part += ", retrying in 5s)"
            else:
                attempt_part += ")"
        return f"{label}... FAILED{error_part}{attempt_part}"
    # skipped
    return "  skipping linked CREATE (upstream mismatch pair)"


def format_results_summary(outcomes: tuple[ActionOutcome, ...]) -> str:
    """Format the results summary line."""
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
        price_ph_day = to_ph_day(bid.price)
        speed = fmt_speed(bid.speed_limit_ph.value)
        lines.append(
            f"{bid.id}  price={price_ph_day} sat/PH/Day  "
            f"limit={speed} PH/s  "
            f"amount={bid.amount_sat} sat  "
            f"{bid.status.name}"
        )
    return "\n".join(lines)


def _fmt_runway(runway: timedelta) -> str:
    if runway == timedelta.max:
        return "∞"
    hours = Decimal(runway.total_seconds()) / Decimal(3600)
    return f"{hours:.1f}h"


def format_balance_check(check: BalanceCheck) -> str:
    """Render the balance check result as a human-readable section."""
    threshold_hours = int(LOW_BALANCE_RUNWAY.total_seconds() // 3600)
    burn_rate_per_hour = check.burn_rate.to(timedelta(hours=1)).amount
    lines = [
        "=== Account Balance ===",
        f"  Available:  {int(check.available_sat):,} sat",
        f"  Required:   {int(check.required_sat):,} sat",
        f"  Burn rate:  {burn_rate_per_hour:,.0f} sat/hour",
        f"  Runway:     {_fmt_runway(check.runway)}",
    ]
    if check.status == BalanceStatus.INSUFFICIENT:
        lines.append("  Status:     INSUFFICIENT — execution aborted")
    elif check.status == BalanceStatus.LOW:
        lines.append(f"  Status:     LOW — runway under {threshold_hours}h")
    else:
        lines.append("  Status:     SUFFICIENT")
    return "\n".join(lines)


def format_set_bids_result(result: SetBidsResult) -> str:
    """Render a complete set-bids run (dry run, aborted, or executed)."""
    plan = result.plan
    has_changes = bool(plan.edits or plan.creates or plan.cancels)
    balance_section = format_balance_check(result.balance_check)

    if result.balance_check.status == BalanceStatus.INSUFFICIENT:
        return "\n".join(
            [
                balance_section,
                "",
                "Execution aborted: insufficient balance to fund planned creates.",
                "",
                format_plan(plan, result.skipped_bids),
            ]
        )

    if result.execution is None:
        return "\n".join([balance_section, "", format_plan(plan, result.skipped_bids)])

    if not has_changes:
        return "\n".join([balance_section, "", "No changes needed."])

    sections = [balance_section, "", "=== Executing Changes ==="]
    sections.extend(format_outcome(o) for o in result.execution.outcomes)
    sections.append("")
    sections.append("=== Results ===")
    sections.append(format_results_summary(result.execution.outcomes))
    sections.append("")
    sections.append("=== Current Bids ===")
    sections.append(format_current_bids(result.execution.final_bids))
    return "\n".join(sections)
