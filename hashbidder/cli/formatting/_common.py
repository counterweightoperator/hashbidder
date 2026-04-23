"""Shared formatting helpers used across multiple CLI feature renderers."""

from decimal import Decimal

from hashbidder.domain.hashrate import HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit


def fmt_speed(value: Decimal) -> str:
    """Format a speed limit value, keeping at least one decimal place."""
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return f"{normalized:.1f}"
    return str(normalized)


def to_ph_day(price: HashratePrice) -> Sats:
    """Convert a hashrate price to sat/PH/Day."""
    return price.to(HashUnit.PH, TimeUnit.DAY).sats
