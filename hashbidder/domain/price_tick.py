"""Price tick primitive for the spot bid market."""

from dataclasses import dataclass
from decimal import Decimal

from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit


@dataclass(frozen=True)
class PriceTick:
    """Minimum price increment enforced by the spot market.

    The wire unit is sat/EH/Day. Every price sent to the API must be
    a multiple of this tick, otherwise the API rejects the order.
    """

    sats: Sats

    def __post_init__(self) -> None:
        if self.sats <= 0:
            raise ValueError(f"PriceTick must be positive, got {self.sats}")

    def is_aligned(self, price: HashratePrice) -> bool:
        """Whether `price` lands exactly on the tick grid."""
        wire = price.to(HashUnit.EH, TimeUnit.DAY)
        return int(wire.sats) % int(self.sats) == 0

    def assert_aligned(self, price: HashratePrice) -> None:
        """Raise ValueError if `price` is not aligned to the tick."""
        if not self.is_aligned(price):
            raise ValueError(
                f"Price {price} is not aligned to tick {int(self.sats)} sat/EH/Day"
            )

    def align_down(self, price: HashratePrice) -> HashratePrice:
        """Round `price` down to the nearest tick."""
        wire = int(price.to(HashUnit.EH, TimeUnit.DAY).sats)
        aligned = (wire // int(self.sats)) * int(self.sats)
        return HashratePrice(
            sats=Sats(aligned),
            per=Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY),
        )

    def add_one(self, price: HashratePrice) -> HashratePrice:
        """Return `price` plus one tick (in EH/Day units)."""
        self.assert_aligned(price)
        wire = int(price.to(HashUnit.EH, TimeUnit.DAY).sats)
        return HashratePrice(
            sats=Sats(wire + int(self.sats)),
            per=Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY),
        )
