"""Prioritized-bucket bid planner.

Three-phase approach over 8 buckets keyed on
(price_vs_target, price_locked, speed_locked):

  Phase 1 — Classify each bid into a bucket.
  Phase 2 — Cancel check: if the minimum physically reachable hashrate
            (speed-locked bids at current speed, speed-free bids at 1 PH/s)
            still exceeds the target, drop bids from the worst buckets
            (overpriced + frozen first) until the target is reachable.
  Phase 3 — Allocate speeds: speed-locked bids keep their speed; bucket
            ABOVE_PRICE_LOCKED_SPEED_FREE (overpriced, price stuck, speed
            adjustable) shrinks to 1 PH/s to limit its weight; the
            remaining budget is distributed across the remaining slots.
"""

from decimal import Decimal
from enum import Enum, auto

from hashbidder.domain.bid_config import MIN_BID_SPEED, BidConfig
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.target_hashrate import BidWithCooldown, distribute_bids


class _Bucket(Enum):
    """All 8 bid states: (price_vs_target, price_locked, speed_locked)."""

    ABOVE_PRICE_LOCKED_SPEED_LOCKED = auto()
    ABOVE_PRICE_LOCKED_SPEED_FREE = auto()
    ABOVE_PRICE_FREE_SPEED_LOCKED = auto()
    ABOVE_PRICE_FREE_SPEED_FREE = auto()
    OK_PRICE_LOCKED_SPEED_LOCKED = auto()
    OK_PRICE_LOCKED_SPEED_FREE = auto()
    OK_PRICE_FREE_SPEED_LOCKED = auto()
    OK_PRICE_FREE_SPEED_FREE = auto()


# Worst bids first: overpriced + fully frozen, then overpriced + speed-free
# (already at 1 PH/s anyway), then progressively better bids.
_CANCEL_PRIORITY = (
    _Bucket.ABOVE_PRICE_LOCKED_SPEED_LOCKED,
    _Bucket.ABOVE_PRICE_LOCKED_SPEED_FREE,
    _Bucket.ABOVE_PRICE_FREE_SPEED_LOCKED,
    _Bucket.ABOVE_PRICE_FREE_SPEED_FREE,
    _Bucket.OK_PRICE_LOCKED_SPEED_LOCKED,
    _Bucket.OK_PRICE_FREE_SPEED_LOCKED,
    _Bucket.OK_PRICE_LOCKED_SPEED_FREE,
    _Bucket.OK_PRICE_FREE_SPEED_FREE,
)


def _classify(bid: BidWithCooldown, target_price: HashratePrice) -> _Bucket:
    bid_sats = bid.bid.price.to(HashUnit.PH, TimeUnit.DAY).sats
    target_sats = target_price.to(HashUnit.PH, TimeUnit.DAY).sats
    above = bid_sats > target_sats
    price_locked = bid.cooldown.price_cooldown
    speed_locked = bid.cooldown.speed_cooldown

    if above:
        if price_locked and speed_locked:
            return _Bucket.ABOVE_PRICE_LOCKED_SPEED_LOCKED
        if price_locked:
            return _Bucket.ABOVE_PRICE_LOCKED_SPEED_FREE
        if speed_locked:
            return _Bucket.ABOVE_PRICE_FREE_SPEED_LOCKED
        return _Bucket.ABOVE_PRICE_FREE_SPEED_FREE
    if price_locked and speed_locked:
        return _Bucket.OK_PRICE_LOCKED_SPEED_LOCKED
    if price_locked:
        return _Bucket.OK_PRICE_LOCKED_SPEED_FREE
    if speed_locked:
        return _Bucket.OK_PRICE_FREE_SPEED_LOCKED
    return _Bucket.OK_PRICE_FREE_SPEED_FREE


def _min_speed_for(bid: BidWithCooldown) -> Decimal:
    if bid.cooldown.speed_cooldown:
        return bid.bid.speed_limit_ph.to(HashUnit.PH, TimeUnit.SECOND).value
    return MIN_BID_SPEED.value


def plan(
    *,
    target_price: HashratePrice,
    target_hashrate: Hashrate,
    max_bids_count: int,
    bids: tuple[BidWithCooldown, ...],
) -> tuple[BidConfig, ...]:
    """Produce a desired bid config using the prioritized-bucket strategy.

    Phase 1 — Classify each existing bid.

    Phase 2 — Cancel from the worst bucket first when the minimum physically
    achievable hashrate exceeds the target. Within a bucket, drop the largest
    speed first.

    Phase 3 — Speed-locked bids keep their current speed. Bucket
    ABOVE_PRICE_LOCKED_SPEED_FREE (overpriced with stuck price) is pinned at
    1 PH/s to minimize its weight in the blend. The remainder of the
    hashrate budget is split evenly across the remaining free slots and
    priced at `target_price`.
    """
    target_ph = target_hashrate.to(HashUnit.PH, TimeUnit.SECOND).value

    bucketed: dict[_Bucket, list[BidWithCooldown]] = {b: [] for b in _Bucket}
    for bid in bids:
        bucketed[_classify(bid, target_price)].append(bid)

    min_total = sum((_min_speed_for(b) for b in bids), Decimal(0))

    if min_total > target_ph:
        for bucket in _CANCEL_PRIORITY:
            candidates = sorted(
                bucketed[bucket],
                key=lambda b: (
                    b.bid.speed_limit_ph.to(HashUnit.PH, TimeUnit.SECOND).value
                ),
                reverse=True,
            )
            survivors: list[BidWithCooldown] = []
            for bid in candidates:
                if min_total <= target_ph:
                    survivors.append(bid)
                    continue
                min_total -= _min_speed_for(bid)
            bucketed[bucket] = survivors
            if min_total <= target_ph:
                break

    configs: list[BidConfig] = []
    fixed_speed_total = Decimal(0)
    slots_used = 0

    for bid in bucketed[_Bucket.ABOVE_PRICE_LOCKED_SPEED_LOCKED]:
        speed = bid.bid.speed_limit_ph.to(HashUnit.PH, TimeUnit.SECOND)
        configs.append(BidConfig(price=bid.bid.price, speed_limit=speed))
        fixed_speed_total += speed.value
        slots_used += 1

    for bid in bucketed[_Bucket.ABOVE_PRICE_FREE_SPEED_LOCKED]:
        speed = bid.bid.speed_limit_ph.to(HashUnit.PH, TimeUnit.SECOND)
        configs.append(BidConfig(price=target_price, speed_limit=speed))
        fixed_speed_total += speed.value
        slots_used += 1

    for bid in bucketed[_Bucket.OK_PRICE_LOCKED_SPEED_LOCKED]:
        speed = bid.bid.speed_limit_ph.to(HashUnit.PH, TimeUnit.SECOND)
        configs.append(BidConfig(price=target_price, speed_limit=speed))
        fixed_speed_total += speed.value
        slots_used += 1

    for bid in bucketed[_Bucket.OK_PRICE_FREE_SPEED_LOCKED]:
        speed = bid.bid.speed_limit_ph.to(HashUnit.PH, TimeUnit.SECOND)
        configs.append(BidConfig(price=target_price, speed_limit=speed))
        fixed_speed_total += speed.value
        slots_used += 1

    bad_assignable_speed = Decimal(0)
    for bid in bucketed[_Bucket.ABOVE_PRICE_LOCKED_SPEED_FREE]:
        configs.append(BidConfig(price=bid.bid.price, speed_limit=MIN_BID_SPEED))
        bad_assignable_speed += MIN_BID_SPEED.value
        slots_used += 1

    committed = fixed_speed_total + bad_assignable_speed
    remaining_ph = max(Decimal(0), target_ph - committed)
    remaining_slots = max(0, max_bids_count - slots_used)

    if remaining_ph > 0 and remaining_slots > 0:
        remaining_hashrate = Hashrate(remaining_ph, HashUnit.PH, TimeUnit.SECOND)
        for speed in distribute_bids(remaining_hashrate, remaining_slots):
            configs.append(BidConfig(price=target_price, speed_limit=speed))

    return tuple(configs)
