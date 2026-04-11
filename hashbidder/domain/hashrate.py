"""Hashrate domain types with hash unit support."""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit

# Number of significant digits used for all hashrate arithmetic.
# Enough to span the full range of hash units (H to EH = 18 orders of magnitude)
# with ~10 digits of meaningful precision on top.
HASHRATE_PRECISION = 28

decimal.getcontext().prec = HASHRATE_PRECISION

# Tolerance for comparisons that involve multiple arithmetic steps.
# Derived from the precision, leaving 4 digits of slack for accumulated rounding.
HASHRATE_TOLERANCE = Decimal(f"1E-{HASHRATE_PRECISION - 4}")


class HashUnit(Enum):
    """Hash count multiplier units."""

    H = 1
    KH = 1_000
    MH = 1_000_000
    GH = 1_000_000_000
    TH = 1_000_000_000_000
    PH = 1_000_000_000_000_000
    EH = 1_000_000_000_000_000_000


@dataclass(frozen=True)
class Hashrate:
    """A hashrate value with explicit hash unit and time period.

    Attributes:
        value: The numeric hashrate value. Must be non-negative.
        hash_unit: The hash count unit (e.g. PH, EH).
        time_unit: The time period denominator (e.g. per second, per day).
    """

    value: Decimal
    hash_unit: HashUnit
    time_unit: TimeUnit

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError(f"Hashrate must be non-negative, got {self.value}")

    def _as_hashes_per_second(self) -> Decimal:
        return (
            self.value * Decimal(self.hash_unit.value) / Decimal(self.time_unit.value)
        )

    def to(self, hash_unit: HashUnit, time_unit: TimeUnit) -> Hashrate:
        """Convert to a different unit and time period.

        Args:
            hash_unit: Target hash unit.
            time_unit: Target time unit.

        Returns:
            An equivalent Hashrate expressed in the new units.
        """
        hps = self._as_hashes_per_second()
        return Hashrate(
            value=hps * Decimal(time_unit.value) / Decimal(hash_unit.value),
            hash_unit=hash_unit,
            time_unit=time_unit,
        )

    def display_unit(self) -> Hashrate:
        """Convert to the largest unit where 1 <= int(value) < 1000.

        For zero hashrate, returns in the smallest unit (H).
        """
        units = sorted(HashUnit, key=lambda u: u.value)
        best = self.to(units[0], self.time_unit)
        for unit in units:
            converted = self.to(unit, self.time_unit)
            int_part = int(converted.value)
            if 1 <= int_part < 1000:
                best = converted
        return best

    def __str__(self) -> str:
        unit = f"{self.hash_unit.name}/{self.time_unit.name.capitalize()}"
        return f"{self.value.normalize()} {unit}"

    def __add__(self, other: Hashrate) -> Hashrate:
        return Hashrate(
            value=self.value + other.to(self.hash_unit, self.time_unit).value,
            hash_unit=self.hash_unit,
            time_unit=self.time_unit,
        )

    def __sub__(self, other: Hashrate) -> Hashrate:
        return Hashrate(
            value=self.value - other.to(self.hash_unit, self.time_unit).value,
            hash_unit=self.hash_unit,
            time_unit=self.time_unit,
        )

    def __lt__(self, other: Hashrate) -> bool:
        return self._as_hashes_per_second() < other._as_hashes_per_second()

    def __le__(self, other: Hashrate) -> bool:
        return self._as_hashes_per_second() <= other._as_hashes_per_second()

    def __gt__(self, other: Hashrate) -> bool:
        return self._as_hashes_per_second() > other._as_hashes_per_second()

    def __ge__(self, other: Hashrate) -> bool:
        return self._as_hashes_per_second() >= other._as_hashes_per_second()


@dataclass(frozen=True)
class HashratePrice:
    """A price denominated in satoshis per unit of hashrate.

    Attributes:
        sats: The cost in satoshis.
        per: The hashrate quantity this price is per.
    """

    sats: Sats
    per: Hashrate

    def __post_init__(self) -> None:
        if self.sats < 0:
            raise ValueError(
                f"HashratePrice must be non-negative, got {self.sats} sats"
            )

    def to(self, hash_unit: HashUnit, time_unit: TimeUnit) -> HashratePrice:
        """Convert to a price per different hashrate unit.

        Scales the sats proportionally so the price per hash-per-second
        remains equivalent.

        Args:
            hash_unit: Target hash unit.
            time_unit: Target time unit.

        Returns:
            An equivalent HashratePrice in the new units.
        """
        old_hps = self.per._as_hashes_per_second()
        new_per = Hashrate(Decimal(1), hash_unit, time_unit)
        new_hps = new_per._as_hashes_per_second()
        scaled_sats = Decimal(self.sats) * new_hps / old_hps
        return HashratePrice(sats=Sats(int(scaled_sats)), per=new_per)

    def __str__(self) -> str:
        return f"{self.sats} sat/{self.per}"
