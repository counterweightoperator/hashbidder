"""Target-hashrate orchestration pieces that depend on client types."""

from dataclasses import dataclass

from hashbidder.clients.braiins import OrderBook, UserBid
from hashbidder.domain.hashrate import HashratePrice
from hashbidder.domain.price_tick import PriceTick


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
