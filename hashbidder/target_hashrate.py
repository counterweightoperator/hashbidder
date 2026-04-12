"""Pure computations for target-hashrate mode."""

from decimal import Decimal

from hashbidder.client import OrderBook
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit


def compute_needed_hashrate(target: Hashrate, current_24h: Hashrate) -> Hashrate:
    """Hashrate to buy now so the 12h-forward 24h average equals target.

    Assumes a 12-hour horizon: if we add `needed` for the next 12 hours, the
    rolling 24h average will land at `target`. Clamped to zero when already
    at or above target.
    """
    twice = target + target
    if current_24h >= twice:
        return Hashrate(Decimal(0), HashUnit.PH, TimeUnit.SECOND)
    return (twice - current_24h).to(HashUnit.PH, TimeUnit.SECOND)


def distribute_bids(needed: Hashrate, max_bids_count: int) -> tuple[Hashrate, ...]:
    """Split a needed hashrate into per-bid speed limits in PH/s.

    Uses as many bids as possible up to `max_bids_count`, with each bid >= 1 PH/s
    and the total summing to `needed` (rounded to 0.01 PH/s precision).
    Returns an empty tuple to mean "cancel all bids".
    """
    if max_bids_count < 1:
        raise ValueError(f"max_bids_count must be >= 1, got {max_bids_count}")

    needed_ph = needed.to(HashUnit.PH, TimeUnit.SECOND).value
    if needed_ph < Decimal("0.5"):
        return ()
    if needed_ph < 1:
        return (Hashrate(Decimal(1), HashUnit.PH, TimeUnit.SECOND),)

    n = min(max_bids_count, int(needed_ph))
    share = (needed_ph / Decimal(n)).quantize(Decimal("0.01"))
    return tuple(Hashrate(share, HashUnit.PH, TimeUnit.SECOND) for _ in range(n))


def find_market_price(orderbook: OrderBook) -> HashratePrice:
    """Lowest-priced bid currently being served, plus one sat.

    Raises:
        ValueError: If no bid in the order book has hr_matched_ph > 0.
    """
    served = [b for b in orderbook.bids if b.hr_matched_ph.value > 0]
    if not served:
        raise ValueError("Order book has no served bids; cannot pick a price")
    cheapest = min(served, key=lambda b: b.price.sats)
    return HashratePrice(
        sats=cheapest.price.sats + Sats(1),
        per=cheapest.price.per,
    )
