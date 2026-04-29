"""Hashrate domain types with hash unit support."""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction

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

    @classmethod
    def from_rate_str(cls, s: str) -> HashUnit:
        """Parse a per-second rate suffix like 'Th/s' or 'GH/s' into a HashUnit.

        Raises:
            ValueError: If the string is not a recognized rate suffix.
        """
        match = _RATE_STR_MAP.get(s)
        if match is None:
            raise ValueError(f"unrecognized hashrate unit: {s!r}")
        return match


# Lookup for rate strings: canonical ("TH/s") and title-case ("Th/s") variants.
_RATE_STR_MAP: dict[str, HashUnit] = {}
for _u in HashUnit:
    _canonical = f"{_u.name}/s"  # e.g. "TH/s", "GH/s", "H/s"
    _title = _u.name.capitalize() + "/s"  # e.g. "Th/s", "Gh/s", "H/s"
    _RATE_STR_MAP[_canonical] = _u
    _RATE_STR_MAP[_title] = _u
del _u, _canonical, _title

_UNITS_ASC = sorted(HashUnit, key=lambda u: u.value)


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
        best = self.to(_UNITS_ASC[0], self.time_unit)
        for unit in _UNITS_ASC:
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

    def __mul__(self, scalar: Fraction) -> Hashrate:
        if scalar < 0:
            raise ValueError(f"Hashrate scalar must be non-negative, got {scalar}")
        scaled = Fraction(self.value) * scalar
        return Hashrate(
            value=Decimal(scaled.numerator) / Decimal(scaled.denominator),
            hash_unit=self.hash_unit,
            time_unit=self.time_unit,
        )

    def __rmul__(self, scalar: Fraction) -> Hashrate:
        return self.__mul__(scalar)

    def __truediv__(self, scalar: Fraction) -> Hashrate:
        if scalar <= 0:
            raise ValueError(f"Hashrate divisor must be positive, got {scalar}")
        scaled = Fraction(self.value) / scalar
        return Hashrate(
            value=Decimal(scaled.numerator) / Decimal(scaled.denominator),
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


@dataclass(frozen=True, eq=False)
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

    def _as_sats_per_hash_per_second(self) -> Fraction:
        # sats / (per.value * per.hash_unit / per.time_unit)
        # = sats * per.time_unit / (per.value * per.hash_unit)
        src_hash = int(self.per.hash_unit.value)
        src_time = int(self.per.time_unit.value)
        src_value = Fraction(self.per.value)
        return Fraction(int(self.sats)) * Fraction(src_time, src_hash) / src_value

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HashratePrice):
            return NotImplemented
        return (
            self._as_sats_per_hash_per_second() == other._as_sats_per_hash_per_second()
        )

    def __hash__(self) -> int:
        return hash(self._as_sats_per_hash_per_second())

    def __lt__(self, other: HashratePrice) -> bool:
        return (
            self._as_sats_per_hash_per_second() < other._as_sats_per_hash_per_second()
        )

    def __le__(self, other: HashratePrice) -> bool:
        return (
            self._as_sats_per_hash_per_second() <= other._as_sats_per_hash_per_second()
        )

    def __gt__(self, other: HashratePrice) -> bool:
        return (
            self._as_sats_per_hash_per_second() > other._as_sats_per_hash_per_second()
        )

    def __ge__(self, other: HashratePrice) -> bool:
        return (
            self._as_sats_per_hash_per_second() >= other._as_sats_per_hash_per_second()
        )

    def to(self, hash_unit: HashUnit, time_unit: TimeUnit) -> HashratePrice:
        """Convert to a price per different hashrate unit.

        Scales the sats proportionally so the price per hash-per-second
        remains equivalent. Computed exactly via Fraction and rounded to
        the nearest sat (banker's rounding) at the end, so converting an
        integer-representable price between units is lossless.

        Args:
            hash_unit: Target hash unit.
            time_unit: Target time unit.

        Returns:
            An equivalent HashratePrice in the new units.
        """
        new_per = Hashrate(Decimal(1), hash_unit, time_unit)
        # Multiplier = new_hps / old_hps
        #            = (1 * dst_hash / dst_time) / (src_value * src_hash / src_time)
        #            = (dst_hash * src_time) / (dst_time * src_hash * src_value)
        src_hash = int(self.per.hash_unit.value)
        src_time = int(self.per.time_unit.value)
        src_value = Fraction(self.per.value)
        dst_hash = int(hash_unit.value)
        dst_time = int(time_unit.value)
        multiplier = Fraction(dst_hash * src_time, dst_time * src_hash) / src_value
        scaled = Fraction(int(self.sats)) * multiplier
        return HashratePrice(sats=Sats(round(scaled)), per=new_per)

    def __str__(self) -> str:
        return f"{self.sats} sat/{self.per}"
