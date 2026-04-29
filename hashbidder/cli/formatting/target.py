"""Formatters for target-hashrate planning inputs and results."""

from __future__ import annotations

from hashbidder.cli.formatting._common import fmt_speed, to_ph_day
from hashbidder.cli.formatting.bids import format_set_bids_result
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.services.target_hashrate import BidWithCooldown
from hashbidder.use_cases import SetBidsTargetResult


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
    price_ph_day = to_ph_day(price)
    lines = [
        "=== Target Hashrate Inputs ===",
        f"  Ocean 24h:    {fmt_speed(ocean_ph)} PH/s",
        f"  Target:       {fmt_speed(target_ph)} PH/s",
        f"  Needed:       {fmt_speed(needed_ph)} PH/s",
        f"  Market price: {price_ph_day} sat/PH/Day",
    ]
    return "\n".join(lines)


def format_set_bids_target_result(result: SetBidsTargetResult) -> str:
    """Render a complete target-hashrate run: inputs followed by set-bids output."""
    inputs = result.inputs
    return "\n".join(
        [
            format_target_inputs(
                ocean_24h=inputs.ocean_24h,
                target=inputs.target,
                needed=inputs.needed_hashrate,
                price=inputs.target_price,
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
            needed=inputs.needed_hashrate,
            price=inputs.target_price,
        ),
        "",
        _format_target_distribution_math(
            target=inputs.target,
            ocean_24h=inputs.ocean_24h,
            needed=inputs.needed_hashrate,
            price=inputs.target_price,
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
    price_ph_day = to_ph_day(price)
    served = Sats(int(price_ph_day) - 1)
    lines = [
        "=== Reasoning ===",
        f"  Price scan:   lowest served bid {served} sat/PH/Day "
        f"→ undercut by 1 sat → {price_ph_day} sat/PH/Day",
        f"  Needed math:  2 * {fmt_speed(target_ph)} (target) "
        f"- {fmt_speed(ocean_ph)} (ocean 24h) = {fmt_speed(needed_ph)} PH/s",
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
        price_ph_day = to_ph_day(bid.price)
        lines.append(
            f"  {bid.id}  price={price_ph_day} sat/PH/Day  "
            f"limit={bid.speed_limit_ph}  → {status}"
        )
    return "\n".join(lines)
