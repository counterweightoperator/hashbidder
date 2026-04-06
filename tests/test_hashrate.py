"""Unit tests for hashrate domain types."""

import pytest

from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit


class TestHashrate:
    """Tests for the Hashrate domain type."""

    def test_rejects_negative_value(self) -> None:
        """Hashrate raises ValueError when constructed with a negative value."""
        with pytest.raises(ValueError, match="non-negative"):
            Hashrate(-1, HashUnit.PH, TimeUnit.SECOND)

    def test_zero_is_valid(self) -> None:
        """Zero is a valid hashrate value."""
        h = Hashrate(0, HashUnit.PH, TimeUnit.SECOND)
        assert h.value == 0

    def test_str(self) -> None:
        """String representation includes value, hash unit, and time unit."""
        h = Hashrate(5, HashUnit.EH, TimeUnit.DAY)
        assert str(h) == "5 EH/Day"

    class TestConversion:
        """Tests for Hashrate.to() unit conversion."""

        def test_same_unit_is_identity(self) -> None:
            """Converting to the same units returns an equal value."""
            h = Hashrate(10, HashUnit.PH, TimeUnit.SECOND)
            assert h.to(HashUnit.PH, TimeUnit.SECOND) == h

        def test_ph_per_second_to_eh_per_day(self) -> None:
            """1 PH/s = 1e15 H/s = 8.64e19 H/day = 86.4 EH/day."""
            h = Hashrate(1, HashUnit.PH, TimeUnit.SECOND)
            converted = h.to(HashUnit.EH, TimeUnit.DAY)
            assert converted.hash_unit == HashUnit.EH
            assert converted.time_unit == TimeUnit.DAY
            assert abs(converted.value - 86.4) < 1e-10

        def test_eh_per_day_to_ph_per_second(self) -> None:
            """86.4 EH/day = 1 PH/s (inverse of ph_per_second_to_eh_per_day)."""
            h = Hashrate(86.4, HashUnit.EH, TimeUnit.DAY)
            converted = h.to(HashUnit.PH, TimeUnit.SECOND)
            assert abs(converted.value - 1.0) < 1e-10

    class TestArithmetic:
        """Tests for Hashrate addition and subtraction."""

        def test_add_same_units(self) -> None:
            """Adding two hashrates with the same units sums their values."""
            a = Hashrate(3, HashUnit.PH, TimeUnit.SECOND)
            b = Hashrate(2, HashUnit.PH, TimeUnit.SECOND)
            result = a + b
            assert result.value == 5
            assert result.hash_unit == HashUnit.PH
            assert result.time_unit == TimeUnit.SECOND

        def test_add_result_uses_left_operand_units(self) -> None:
            """Addition result is expressed in the left operand's units."""
            a = Hashrate(1, HashUnit.EH, TimeUnit.DAY)
            b = Hashrate(1, HashUnit.EH, TimeUnit.DAY)
            result = a + b
            assert result.hash_unit == HashUnit.EH
            assert result.time_unit == TimeUnit.DAY
            assert result.value == 2

        def test_add_mixed_units(self) -> None:
            """Adding hashrates in different units converts the right operand first."""
            a = Hashrate(1, HashUnit.EH, TimeUnit.DAY)
            b = Hashrate(1, HashUnit.EH, TimeUnit.DAY).to(HashUnit.PH, TimeUnit.SECOND)
            result = a + b
            assert result.hash_unit == HashUnit.EH
            assert result.time_unit == TimeUnit.DAY
            assert abs(result.value - 2.0) < 1e-10

        def test_sub_same_units(self) -> None:
            """Subtraction with same units differences the values."""
            a = Hashrate(5, HashUnit.TH, TimeUnit.SECOND)
            b = Hashrate(3, HashUnit.TH, TimeUnit.SECOND)
            result = a - b
            assert result.value == 2
            assert result.hash_unit == HashUnit.TH
            assert result.time_unit == TimeUnit.SECOND

        def test_sub_to_zero_is_valid(self) -> None:
            """Subtracting a hashrate from itself yields zero."""
            a = Hashrate(1, HashUnit.PH, TimeUnit.SECOND)
            result = a - a
            assert result.value == 0

        def test_sub_to_negative_raises(self) -> None:
            """Subtraction that would yield a negative value raises ValueError."""
            a = Hashrate(1, HashUnit.PH, TimeUnit.SECOND)
            b = Hashrate(2, HashUnit.PH, TimeUnit.SECOND)
            with pytest.raises(ValueError, match="non-negative"):
                a - b

    class TestComparison:
        """Tests for Hashrate comparison operators across units."""

        def test_less_than_same_units(self) -> None:
            """Smaller value compares less than larger value with same units."""
            assert Hashrate(1, HashUnit.PH, TimeUnit.SECOND) < Hashrate(
                2, HashUnit.PH, TimeUnit.SECOND
            )

        def test_greater_than_across_units(self) -> None:
            """1 PH/s = 86.4 EH/day, so 1 PH/s is greater than 1 EH/day."""
            assert Hashrate(1, HashUnit.PH, TimeUnit.SECOND) > Hashrate(
                1, HashUnit.EH, TimeUnit.DAY
            )

        def test_equal_across_units(self) -> None:
            """Equivalent hashrates in different units compare as equal."""
            a = Hashrate(1, HashUnit.EH, TimeUnit.DAY)
            b = a.to(HashUnit.PH, TimeUnit.SECOND)
            assert a >= b
            assert a <= b
            assert not (a < b)
            assert not (a > b)


class TestHashratePrice:
    """Tests for the HashratePrice domain type."""

    def test_rejects_negative_sats(self) -> None:
        """HashratePrice raises ValueError when constructed with negative sats."""
        with pytest.raises(ValueError, match="non-negative"):
            HashratePrice(sats=Sats(-1), per=Hashrate(1, HashUnit.EH, TimeUnit.DAY))

    def test_zero_sats_is_valid(self) -> None:
        """Zero sats is a valid hashrate price."""
        p = HashratePrice(sats=Sats(0), per=Hashrate(1, HashUnit.EH, TimeUnit.DAY))
        assert p.sats == 0

    def test_str(self) -> None:
        """String representation shows sats and the per-hashrate unit."""
        p = HashratePrice(sats=Sats(100), per=Hashrate(1, HashUnit.EH, TimeUnit.DAY))
        assert str(p) == "100 sat/1 EH/Day"
