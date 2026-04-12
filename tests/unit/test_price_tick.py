"""Tests for the PriceTick primitive."""

from decimal import Decimal

import pytest

from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.price_tick import PriceTick
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit

EH_DAY = Hashrate(Decimal(1), HashUnit.EH, TimeUnit.DAY)
PH_DAY = Hashrate(Decimal(1), HashUnit.PH, TimeUnit.DAY)


def _eh_day(sats: int) -> HashratePrice:
    return HashratePrice(sats=Sats(sats), per=EH_DAY)


def _ph_day(sats: int) -> HashratePrice:
    return HashratePrice(sats=Sats(sats), per=PH_DAY)


class TestPriceTickConstruction:
    """Tests for PriceTick __post_init__."""

    def test_positive_is_valid(self) -> None:
        """A positive sats value is accepted."""
        assert PriceTick(sats=Sats(1)).sats == Sats(1)

    def test_zero_rejected(self) -> None:
        """Zero is rejected as non-positive."""
        with pytest.raises(ValueError, match="positive"):
            PriceTick(sats=Sats(0))

    def test_negative_rejected(self) -> None:
        """A negative value is rejected."""
        with pytest.raises(ValueError, match="positive"):
            PriceTick(sats=Sats(-100))


class TestPriceTickIsAligned:
    """Tests for PriceTick.is_aligned."""

    def test_aligned_eh_day(self) -> None:
        """A multiple of the tick in EH/Day units is aligned."""
        tick = PriceTick(sats=Sats(1000))
        assert tick.is_aligned(_eh_day(46350000))

    def test_unaligned_eh_day(self) -> None:
        """One sat above a tick boundary is not aligned."""
        tick = PriceTick(sats=Sats(1000))
        assert not tick.is_aligned(_eh_day(46350001))

    def test_aligned_across_units(self) -> None:
        """A price in sat/PH/Day is aligned iff its sat/EH/Day form is."""
        tick = PriceTick(sats=Sats(1000))
        # 900 sat/PH/Day == 900_000 sat/EH/Day (a multiple of 1000).
        assert tick.is_aligned(_ph_day(900))

    def test_assert_aligned_raises_on_misalignment(self) -> None:
        """assert_aligned raises ValueError when the price is off-grid."""
        tick = PriceTick(sats=Sats(1000))
        with pytest.raises(ValueError, match="not aligned to tick"):
            tick.assert_aligned(_eh_day(46350001))


class TestPriceTickAlignDown:
    """Tests for PriceTick.align_down."""

    def test_already_aligned_unchanged(self) -> None:
        """An aligned price is returned unchanged."""
        tick = PriceTick(sats=Sats(1000))
        result = tick.align_down(_eh_day(46350000))
        assert int(result.sats) == 46350000

    def test_rounds_down(self) -> None:
        """An off-grid price is floored to the tick below."""
        tick = PriceTick(sats=Sats(1000))
        result = tick.align_down(_eh_day(46350999))
        assert int(result.sats) == 46350000

    def test_align_down_from_ph_day(self) -> None:
        """Aligning a sat/PH/Day price returns it in EH/Day."""
        tick = PriceTick(sats=Sats(1000))
        result = tick.align_down(_ph_day(900))
        assert int(result.sats) == 900_000
        assert result.per == EH_DAY


class TestPriceTickAddOne:
    """Tests for PriceTick.add_one."""

    def test_add_one_increments_by_tick(self) -> None:
        """add_one returns price + one tick in EH/Day units."""
        tick = PriceTick(sats=Sats(1000))
        result = tick.add_one(_eh_day(46350000))
        assert int(result.sats) == 46351000

    def test_add_one_rejects_unaligned_input(self) -> None:
        """add_one refuses to operate on an unaligned price."""
        tick = PriceTick(sats=Sats(1000))
        with pytest.raises(ValueError, match="not aligned"):
            tick.add_one(_eh_day(46350001))
