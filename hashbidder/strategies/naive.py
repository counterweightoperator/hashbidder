"""Naive cooldown-aware bid planner (the default strategy)."""

from decimal import Decimal

from hashbidder.domain.bid_config import BidConfig
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.target_hashrate import BidWithCooldown, distribute_bids


def plan(
    *,
    target_price: HashratePrice,
    target_hashrate: Hashrate,
    max_bids_count: int,
    bids: tuple[BidWithCooldown, ...],
) -> tuple[BidConfig, ...]:
    """Build a bid plan that respects per-bid cooldown constraints.

    Rules:
      - speed_cooldown=True: keep the bid's current speed limit (cannot lower).
        Such a bid consumes one slot from `max_bids_count` and its current
        speed is subtracted from `target_hashrate`.
      - price_cooldown=True (and not speed_cooldown): keep the bid's current
        price; speed is freely re-assigned from the remaining distribution.
      - Bids with no cooldown are treated as fresh slots at `target_price`.

    The remaining hashrate budget is split via `distribute_bids` and assigned
    first to price-locked bids (preserving their old price), then to brand-new
    slots at `target_price`.
    """
    speed_locked = [b for b in bids if b.cooldown.speed_cooldown]
    price_locked_only = [
        b for b in bids if b.cooldown.price_cooldown and not b.cooldown.speed_cooldown
    ]

    locked_speed_total = Hashrate(Decimal(0), HashUnit.PH, TimeUnit.SECOND)
    for entry in speed_locked:
        locked_speed_total = locked_speed_total + entry.bid.speed_limit_ph

    locked_entries = tuple(
        BidConfig(
            price=entry.bid.price if entry.cooldown.price_cooldown else target_price,
            speed_limit=entry.bid.speed_limit_ph,
        )
        for entry in speed_locked
    )

    if target_hashrate > locked_speed_total:
        remaining = target_hashrate - locked_speed_total
    else:
        remaining = Hashrate(Decimal(0), HashUnit.PH, TimeUnit.SECOND)

    remaining_slots = max(0, max_bids_count - len(speed_locked))
    speeds = distribute_bids(remaining, remaining_slots) if remaining_slots else ()

    free_entries: list[BidConfig] = []
    for i, speed in enumerate(speeds):
        if i < len(price_locked_only):
            entry = price_locked_only[i]
            free_entries.append(BidConfig(price=entry.bid.price, speed_limit=speed))
        else:
            free_entries.append(BidConfig(price=target_price, speed_limit=speed))

    return locked_entries + tuple(free_entries)
