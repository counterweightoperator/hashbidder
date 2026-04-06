"""Hashrate domain types with hash unit support."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit


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

    value: float
    hash_unit: HashUnit
    time_unit: TimeUnit

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError(f"Hashrate must be non-negative, got {self.value}")

    def _as_hashes_per_second(self) -> float:
        return self.value * self.hash_unit.value / self.time_unit.value

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
            value=hps * time_unit.value / hash_unit.value,
            hash_unit=hash_unit,
            time_unit=time_unit,
        )

    def __str__(self) -> str:
        return f"{self.value} {self.hash_unit.name}/{self.time_unit.name.capitalize()}"

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

    def __str__(self) -> str:
        return f"{self.sats} sat/{self.per}"
