"""Pure computations for target-hashrate mode."""

from dataclasses import dataclass
from decimal import Decimal

from hashbidder.client import OrderBook, UserBid
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.price_tick import PriceTick
from hashbidder.domain.time_unit import TimeUnit


def compute_needed_hashrate(target: Hashrate, current_24h: Hashrate) -> Hashrate:
    """Hashrate to buy now so the 12h-forward 24h average equals target.

    Assumes a 12-hour horizon: if we add `needed` for the next 12 hours, the
    rolling 24h average will land at `target`. Clamped to zero when already
    at or above target. Can't be between 0 and 1.
    """
    twice = target + target
    if current_24h >= twice:
        return Hashrate(Decimal(0), HashUnit.PH, TimeUnit.SECOND)

    needed_hashrate = (twice - current_24h).to(HashUnit.PH, TimeUnit.SECOND)

    if needed_hashrate < Hashrate(Decimal("0.5"), HashUnit.PH, TimeUnit.SECOND):
        needed_hashrate = Hashrate(Decimal(0), HashUnit.PH, TimeUnit.SECOND)
    elif needed_hashrate < Hashrate(Decimal(1), HashUnit.PH, TimeUnit.SECOND):
        needed_hashrate = Hashrate(Decimal(1), HashUnit.PH, TimeUnit.SECOND)

    return needed_hashrate


@dataclass(frozen=True)
class BidWithCooldown:
    """A bid paired with its current cooldown status."""

    bid: UserBid
    is_price_in_cooldown: bool
    is_speed_in_cooldown: bool


def find_market_price(orderbook: OrderBook, tick: PriceTick) -> HashratePrice:
    """Lowest served bid, undercut (from above) by one price tick.

    The cheapest served price is aligned down to the tick grid first to
    guarantee the result lands on a valid tick.

    Raises:
        ValueError: If no bid in the order book has hr_matched_ph > 0.
    """
    served = [b for b in orderbook.bids if b.hr_matched_ph.value > 0]
    if not served:
        raise ValueError("Order book has no served bids; cannot pick a price")
    cheapest = min(served, key=lambda b: b.price.sats)
    return tick.add_one(tick.align_down(cheapest.price))
