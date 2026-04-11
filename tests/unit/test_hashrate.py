"""Unit and property-based tests for hashrate domain types."""

from decimal import Decimal

import pytest
from hypothesis import assume, given, strategies
from hypothesis.strategies import DrawFn, composite

from hashbidder.domain.hashrate import (
    HASHRATE_TOLERANCE,
    Hashrate,
    HashratePrice,
    HashUnit,
)
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit

# Bound values to avoid overflow when multiplied by the largest hash unit (EH = 1e18).
_MAX_HASHRATE_VALUE = Decimal("1E+20")

_GENERATED_PLACES = 10

_hashrate_value = strategies.decimals(
    min_value=Decimal("0"),
    max_value=_MAX_HASHRATE_VALUE,
    allow_nan=False,
    allow_infinity=False,
    places=_GENERATED_PLACES,
)
_negative_decimal = strategies.decimals(
    max_value=Decimal("-1E-10"),
    allow_nan=False,
    allow_infinity=False,
)
_hash_unit = strategies.sampled_from(HashUnit)
_time_unit = strategies.sampled_from(TimeUnit)


@composite
def hashrates(draw: DrawFn) -> Hashrate:
    """Strategy that generates arbitrary valid Hashrate instances."""
    return Hashrate(
        value=draw(_hashrate_value),
        hash_unit=draw(_hash_unit),
        time_unit=draw(_time_unit),
    )


def _within_tolerance(a: Decimal, b: Decimal) -> bool:
    """Return True if a and b agree within HASHRATE_TOLERANCE."""
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    if scale == 0:
        return True
    return abs(a - b) / scale <= HASHRATE_TOLERANCE


class TestHashrate:
    """Tests for the Hashrate domain type."""

    def test_zero_is_valid(self) -> None:
        """Zero is a valid hashrate value."""
        h = Hashrate(Decimal("0"), HashUnit.PH, TimeUnit.SECOND)
        assert h.value == Decimal("0")

    def test_str(self) -> None:
        """String representation includes value, hash unit, and time unit."""
        h = Hashrate(Decimal("5"), HashUnit.EH, TimeUnit.DAY)
        assert str(h) == "5 EH/Day"

    @given(_negative_decimal, _hash_unit, _time_unit)
    def test_negative_value_always_rejected(
        self, value: Decimal, hash_unit: HashUnit, time_unit: TimeUnit
    ) -> None:
        """Any negative value raises ValueError regardless of units."""
        with pytest.raises(ValueError, match="non-negative"):
            Hashrate(value, hash_unit, time_unit)

    class TestConversion:
        """Tests for Hashrate.to() unit conversion."""

        def test_same_unit_is_identity(self) -> None:
            """Converting to the same units returns an equal value."""
            h = Hashrate(Decimal("10"), HashUnit.PH, TimeUnit.SECOND)
            assert h.to(HashUnit.PH, TimeUnit.SECOND) == h

        def test_ph_per_second_to_eh_per_day(self) -> None:
            """1 PH/s = 1e15 H/s = 8.64e19 H/day = 86.4 EH/day."""
            h = Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.SECOND)
            converted = h.to(HashUnit.EH, TimeUnit.DAY)
            assert converted.hash_unit == HashUnit.EH
            assert converted.time_unit == TimeUnit.DAY
            assert converted.value == Decimal("86.4")

        def test_eh_per_day_to_ph_per_second(self) -> None:
            """86.4 EH/day = 1 PH/s (inverse of ph_per_second_to_eh_per_day)."""
            h = Hashrate(Decimal("86.4"), HashUnit.EH, TimeUnit.DAY)
            converted = h.to(HashUnit.PH, TimeUnit.SECOND)
            assert converted.value == Decimal("1")

        @given(hashrates(), _hash_unit, _time_unit)
        def test_conversion_preserves_physical_magnitude(
            self, h: Hashrate, target_hash: HashUnit, target_time: TimeUnit
        ) -> None:
            """Converting to any unit does not change the underlying H/s magnitude."""
            converted = h.to(target_hash, target_time)
            assert _within_tolerance(
                h._as_hashes_per_second(),
                converted._as_hashes_per_second(),
            )

        @given(hashrates(), _hash_unit, _time_unit)
        def test_conversion_roundtrip(
            self, h: Hashrate, target_hash: HashUnit, target_time: TimeUnit
        ) -> None:
            """Converting to another unit and back recovers the original value."""
            roundtripped = h.to(target_hash, target_time).to(h.hash_unit, h.time_unit)
            assert _within_tolerance(h.value, roundtripped.value)

    class TestArithmetic:
        """Tests for Hashrate addition and subtraction."""

        def test_add_same_units(self) -> None:
            """Adding two hashrates with the same units sums their values."""
            a = Hashrate(Decimal("3"), HashUnit.PH, TimeUnit.SECOND)
            b = Hashrate(Decimal("2"), HashUnit.PH, TimeUnit.SECOND)
            result = a + b
            assert result.value == Decimal("5")
            assert result.hash_unit == HashUnit.PH
            assert result.time_unit == TimeUnit.SECOND

        def test_add_mixed_units(self) -> None:
            """Adding hashrates in different units converts the right operand first."""
            a = Hashrate(Decimal("1"), HashUnit.EH, TimeUnit.DAY)
            b = Hashrate(Decimal("1"), HashUnit.EH, TimeUnit.DAY).to(
                HashUnit.PH, TimeUnit.SECOND
            )
            result = a + b
            assert result.hash_unit == HashUnit.EH
            assert result.time_unit == TimeUnit.DAY
            assert result.value == Decimal("2")

        def test_sub_same_units(self) -> None:
            """Subtraction with same units differences the values."""
            a = Hashrate(Decimal("5"), HashUnit.TH, TimeUnit.SECOND)
            b = Hashrate(Decimal("3"), HashUnit.TH, TimeUnit.SECOND)
            result = a - b
            assert result.value == Decimal("2")
            assert result.hash_unit == HashUnit.TH
            assert result.time_unit == TimeUnit.SECOND

        def test_sub_to_zero_is_valid(self) -> None:
            """Subtracting a hashrate from itself yields zero."""
            a = Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.SECOND)
            result = a - a
            assert result.value == Decimal("0")

        def test_sub_to_negative_raises(self) -> None:
            """Subtraction that would yield a negative value raises ValueError."""
            a = Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.SECOND)
            b = Hashrate(Decimal("2"), HashUnit.PH, TimeUnit.SECOND)
            with pytest.raises(ValueError, match="non-negative"):
                a - b

        @given(hashrates(), hashrates())
        def test_addition_commutativity(self, a: Hashrate, b: Hashrate) -> None:
            """Addition is commutative: a+b and b+a have the same physical magnitude."""
            assert _within_tolerance(
                (a + b)._as_hashes_per_second(),
                (b + a)._as_hashes_per_second(),
            )

        @given(hashrates(), hashrates())
        def test_subtraction_reverses_addition(self, a: Hashrate, b: Hashrate) -> None:
            """(a + b) - b recovers a, exact within our decimal precision."""
            b_in_a_units = b.to(a.hash_unit, a.time_unit).value
            # With HASHRATE_PRECISION=28, rounding error in (a+b)-b relative to a
            # is approximately (b/a) * 10^-(HASHRATE_PRECISION-1) = (b/a) * 10^-27.
            # For that to be within HASHRATE_TOLERANCE=10^-24 we need b/a <= 10^3.
            # Use a 10x safety margin to stay clear of the boundary.
            assume(b_in_a_units <= a.value * Decimal("100"))
            result = (a + b) - b
            assert _within_tolerance(
                result._as_hashes_per_second(),
                a._as_hashes_per_second(),
            )

        @given(hashrates(), hashrates())
        def test_addition_is_monotone(self, a: Hashrate, b: Hashrate) -> None:
            """Addition is monotone: a + b >= a since b is non-negative."""
            assert (a + b) >= a

    class TestComparison:
        """Tests for Hashrate comparison operators across units."""

        def test_less_than_same_units(self) -> None:
            """Smaller value compares less than larger value with same units."""
            assert Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.SECOND) < Hashrate(
                Decimal("2"), HashUnit.PH, TimeUnit.SECOND
            )

        def test_greater_than_across_units(self) -> None:
            """1 PH/s = 86.4 EH/day, so 1 PH/s is greater than 1 EH/day."""
            assert Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.SECOND) > Hashrate(
                Decimal("1"), HashUnit.EH, TimeUnit.DAY
            )

        def test_equal_across_units(self) -> None:
            """Equivalent hashrates in different units compare as equal."""
            a = Hashrate(Decimal("1"), HashUnit.EH, TimeUnit.DAY)
            b = a.to(HashUnit.PH, TimeUnit.SECOND)
            assert a >= b
            assert a <= b
            assert not (a < b)
            assert not (a > b)

        @given(hashrates(), hashrates())
        def test_ordering_antisymmetry(self, a: Hashrate, b: Hashrate) -> None:
            """If a > b then b < a, and vice versa."""
            assume(
                not _within_tolerance(
                    a._as_hashes_per_second(),
                    b._as_hashes_per_second(),
                )
            )
            if a > b:
                assert b < a
            else:
                assert a < b


class TestDisplayUnit:
    """Tests for Hashrate.display_unit() auto-selection."""

    def test_large_gh_converts_to_ph(self) -> None:
        """1,885,800 GH/s should display as 1.8858 PH/s."""
        h = Hashrate(Decimal("1885800"), HashUnit.GH, TimeUnit.SECOND)
        result = h.display_unit()
        assert result.hash_unit == HashUnit.PH
        assert result.value == Decimal("1.8858")

    def test_500_gh_stays_gh(self) -> None:
        """500 GH/s already has int part in [1, 1000), stays as GH/s."""
        h = Hashrate(Decimal("500"), HashUnit.GH, TimeUnit.SECOND)
        result = h.display_unit()
        assert result.hash_unit == HashUnit.GH
        assert result.value == Decimal("500")

    def test_zero_hashrate(self) -> None:
        """Zero hashrate stays at the smallest unit (H)."""
        h = Hashrate(Decimal("0"), HashUnit.TH, TimeUnit.SECOND)
        result = h.display_unit()
        assert result.hash_unit == HashUnit.H
        assert result.value == Decimal("0")

    def test_already_in_best_unit(self) -> None:
        """A value already in the right unit stays there."""
        h = Hashrate(Decimal("42"), HashUnit.TH, TimeUnit.SECOND)
        result = h.display_unit()
        assert result.hash_unit == HashUnit.TH
        assert result.value == Decimal("42")

    def test_preserves_time_unit(self) -> None:
        """display_unit keeps the original time unit."""
        h = Hashrate(Decimal("1885800"), HashUnit.GH, TimeUnit.DAY)
        result = h.display_unit()
        assert result.time_unit == TimeUnit.DAY


class TestHashratePrice:
    """Tests for the HashratePrice domain type."""

    def test_zero_sats_is_valid(self) -> None:
        """Zero sats is a valid hashrate price."""
        p = HashratePrice(
            sats=Sats(0), per=Hashrate(Decimal("1"), HashUnit.EH, TimeUnit.DAY)
        )
        assert p.sats == 0

    def test_str(self) -> None:
        """String representation shows sats and the per-hashrate unit."""
        p = HashratePrice(
            sats=Sats(100), per=Hashrate(Decimal("1"), HashUnit.EH, TimeUnit.DAY)
        )
        assert str(p) == "100 sat/1 EH/Day"

    @given(strategies.integers(max_value=-1), hashrates())
    def test_negative_sats_always_rejected(self, sats: int, per: Hashrate) -> None:
        """Any negative sats value raises ValueError."""
        with pytest.raises(ValueError, match="non-negative"):
            HashratePrice(sats=Sats(sats), per=per)

    @given(strategies.integers(min_value=0), hashrates())
    def test_non_negative_sats_always_accepted(self, sats: int, per: Hashrate) -> None:
        """Any non-negative sats value constructs successfully."""
        price = HashratePrice(sats=Sats(sats), per=per)
        assert price.sats == sats
