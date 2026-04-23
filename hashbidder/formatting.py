"""Output formatting for reconciliation plans and execution results."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import httpx

from hashbidder.bid_runner import ActionOutcome, ActionStatus, SetBidsResult
from hashbidder.braiins_client import UserBid
from hashbidder.clients.ocean import AccountStats
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
from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.hashvalue import HashvalueComponents
from hashbidder.target_hashrate import BidWithCooldown
from hashbidder.use_cases import SetBidsTargetResult


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
    old_price = _to_ph_day(edit.bid.price)
    new_price = _to_ph_day(edit.new_price)

    if edit.price_changed:
        price_line = f"  price:       {old_price} \u2192 {new_price} sat/PH/Day"
    else:
        price_line = f"  price:       {old_price} sat/PH/Day (unchanged)"

    if edit.speed_limit_changed:
        old_speed = _fmt_speed(edit.bid.speed_limit_ph.value)
        new_speed = _fmt_speed(edit.new_speed_limit_ph.value)
        speed_line = f"  speed_limit: {old_speed} \u2192 {new_speed} PH/s"
    else:
        speed_line = (
            f"  speed_limit: "
            f"{_fmt_speed(edit.bid.speed_limit_ph.value)} PH/s (unchanged)"
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
            old = _to_ph_day(edit.bid.price)
            changes.append(f"price {old}\u2192{price}")
        if edit.speed_limit_changed:
            old_s = _fmt_speed(edit.bid.speed_limit_ph.value)
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

    for bid in plan.unchanged:
        price = _to_ph_day(bid.price)
        speed = _fmt_speed(bid.speed_limit_ph.value)
        state_lines.append(
            _format_final_state_line(price, speed, bid.amount_sat, "UNCHANGED")
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


def _action_label(action: CancelAction | EditAction | CreateAction) -> str:
    """Build a human-readable label for an action."""
    if isinstance(action, CancelAction):
        return f"CANCEL {action.bid.id}"
    if isinstance(action, EditAction):
        return f"EDIT {action.bid.id}"
    price = _to_ph_day(action.config.price)
    speed = _fmt_speed(action.config.speed_limit.value)
    return f"CREATE {price} sat/PH/Day {speed} PH/s"


def format_outcome(outcome: ActionOutcome) -> str:
    """Format a single action outcome for real-time execution output."""
    label = _action_label(outcome.action)
    if outcome.status == ActionStatus.SUCCEEDED:
        suffix = "OK"
        if outcome.created_id:
            suffix = f"OK \u2192 {outcome.created_id}"
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
        price_ph_day = _to_ph_day(bid.price)
        speed = _fmt_speed(bid.speed_limit_ph.value)
        lines.append(
            f"{bid.id}  price={price_ph_day} sat/PH/Day  "
            f"limit={speed} PH/s  "
            f"amount={bid.amount_sat} sat  "
            f"{bid.status.name}"
        )
    return "\n".join(lines)


def format_ocean_stats(stats: AccountStats, address: BtcAddress) -> str:
    """Format Ocean account stats for display.

    If all hashrate values are zero, returns an informative message
    instead of the stats table.
    """
    all_zero = all(w.hashrate.value == 0 for w in stats.windows)
    if all_zero:
        return f"No stats found for {address} on Ocean."

    lines = [f"Ocean stats for {address.truncated()}", ""]
    for w in stats.windows:
        display = w.hashrate.display_unit()
        label = w.window.value
        value_str = f"{display.value:.2f} {display.hash_unit.name}/s"
        lines.append(f"  {label:>6s}    {value_str}")

    return "\n".join(lines)


def format_target_inputs(
    ocean_24h: Hashrate,
    target: Hashrate,
    needed: Hashrate,
    price: HashratePrice,
) -> str:
    """Render the inputs that drove a target-hashrate planning run."""
    ocean_ph = ocean_24h.to(HashUnit.PH, TimeUnit.SECOND).value
    target_ph = target.to(HashUnit.PH, TimeUnit.SECOND).value
    needed_ph = needed.to(HashUnit.PH, TimeUnit.SECOND).value
    price_ph_day = _to_ph_day(price)
    lines = [
        "=== Target Hashrate Inputs ===",
        f"  Ocean 24h:    {_fmt_speed(ocean_ph)} PH/s",
        f"  Target:       {_fmt_speed(target_ph)} PH/s",
        f"  Needed:       {_fmt_speed(needed_ph)} PH/s",
        f"  Market price: {price_ph_day} sat/PH/Day",
    ]
    return "\n".join(lines)


def _fmt_runway(runway: timedelta) -> str:
    if runway == timedelta.max:
        return "\u221e"
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
        lines.append("  Status:     INSUFFICIENT \u2014 execution aborted")
    elif check.status == BalanceStatus.LOW:
        lines.append(f"  Status:     LOW \u2014 runway under {threshold_hours}h")
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


def format_set_bids_target_result(result: SetBidsTargetResult) -> str:
    """Render a complete target-hashrate run: inputs followed by set-bids output."""
    inputs = result.inputs
    return "\n".join(
        [
            format_target_inputs(
                ocean_24h=inputs.ocean_24h,
                target=inputs.target,
                needed=inputs.needed,
                price=inputs.price,
            ),
            "",
            format_set_bids_result(result.set_bids_result),
        ]
    )


def format_set_bids_target_result_verbose(result: SetBidsTargetResult) -> str:
    """Render a target-hashrate run with the reasoning behind every decision."""
    inputs = result.inputs
    sections = [
        format_target_inputs(
            ocean_24h=inputs.ocean_24h,
            target=inputs.target,
            needed=inputs.needed,
            price=inputs.price,
        ),
        "",
        _format_target_distribution_math(
            target=inputs.target,
            ocean_24h=inputs.ocean_24h,
            needed=inputs.needed,
            price=inputs.price,
        ),
        "",
        _format_target_cooldowns(inputs.bids_with_cooldowns),
        "",
        format_set_bids_result(result.set_bids_result),
    ]
    return "\n".join(sections)


def _format_target_distribution_math(
    target: Hashrate,
    ocean_24h: Hashrate,
    needed: Hashrate,
    price: HashratePrice,
) -> str:
    target_ph = target.to(HashUnit.PH, TimeUnit.SECOND).value
    ocean_ph = ocean_24h.to(HashUnit.PH, TimeUnit.SECOND).value
    needed_ph = needed.to(HashUnit.PH, TimeUnit.SECOND).value
    price_ph_day = _to_ph_day(price)
    served = Sats(int(price_ph_day) - 1)
    lines = [
        "=== Reasoning ===",
        f"  Price scan:   lowest served bid {served} sat/PH/Day "
        f"→ undercut by 1 sat → {price_ph_day} sat/PH/Day",
        f"  Needed math:  2 * {_fmt_speed(target_ph)} (target) "
        f"- {_fmt_speed(ocean_ph)} (ocean 24h) = {_fmt_speed(needed_ph)} PH/s",
        "(min 1 PH/s each, quantized to 0.01 PH/s)",
    ]
    return "\n".join(lines)


def _format_target_cooldowns(annotated: tuple[BidWithCooldown, ...]) -> str:
    lines = ["=== Cooldown Status ==="]
    if not annotated:
        lines.append("  (no existing bids)")
        return "\n".join(lines)
    for entry in annotated:
        bid = entry.bid
        if entry.is_price_in_cooldown and entry.is_speed_in_cooldown:
            status = "price+speed locked"
        elif entry.is_price_in_cooldown:
            status = "price locked (speed free)"
        elif entry.is_speed_in_cooldown:
            status = "speed locked (price free)"
        else:
            status = "free"
        price_ph_day = _to_ph_day(bid.price)
        lines.append(
            f"  {bid.id}  price={price_ph_day} sat/PH/Day  "
            f"limit={bid.speed_limit_ph}  → {status}"
        )
    return "\n".join(lines)


def format_hashvalue(components: HashvalueComponents) -> str:
    """Format hashvalue as a single line."""
    return f"Hashvalue: {components.hashvalue.sats} sat/PH/Day"


def format_hashvalue_verbose(
    components: HashvalueComponents, mempool_url: httpx.URL
) -> str:
    """Format hashvalue with all intermediate components."""
    lines = [
        format_hashvalue(components),
        "",
        f"  Tip height:       {components.tip_height}",
        f"  Block subsidy:    {components.subsidy} sat",
        f"  Total fees (2016): {components.total_fees} sat",
        f"  Total reward (2016): {components.total_reward} sat",
        f"  Difficulty:       {components.difficulty}",
        f"  Network hashrate: {components.network_hashrate:.2E} H/s",
        f"  Mempool instance: {mempool_url}",
    ]
    return "\n".join(lines)
