"""Tests for Progress domain type."""

from decimal import Decimal

import pytest
from hypothesis import given, strategies

from hashbidder.domain.progress import Progress


class TestProgress:
    """Tests for the Progress domain type."""

    def test_zero_is_valid(self) -> None:
        """Zero is a valid progress value."""
        p = Progress(Decimal("0"))
        assert p.value == Decimal("0")

    def test_one_is_valid(self) -> None:
        """1 (100%) is a valid progress value."""
        p = Progress(Decimal("1"))
        assert p.value == Decimal("1")

    def test_str(self) -> None:
        """String representation shows value as percentage."""
        p = Progress(Decimal("0.425"))
        assert str(p) == "42.5%"

    def test_from_percentage(self) -> None:
        """from_percentage converts 0..100 to 0..1."""
        p = Progress.from_percentage(Decimal("42.5"))
        assert p.value == Decimal("0.425")

    def test_percentage_property(self) -> None:
        """Percentage property converts back to 0..100."""
        p = Progress(Decimal("0.425"))
        assert p.percentage == Decimal("42.5")

    def test_equality(self) -> None:
        """Two Progress with the same value are equal."""
        a = Progress(Decimal("0.5"))
        b = Progress(Decimal("0.5"))
        assert a == b

    def test_inequality(self) -> None:
        """Two Progress with different values are not equal."""
        a = Progress(Decimal("0.5"))
        b = Progress(Decimal("0.51"))
        assert a != b

    @given(
        strategies.decimals(
            min_value=Decimal("-1000"),
            max_value=Decimal("-0.001"),
            allow_nan=False,
            allow_infinity=False,
        )
    )
    def test_negative_always_rejected(self, value: Decimal) -> None:
        """Any negative value raises ValueError."""
        with pytest.raises(ValueError, match="between 0 and 1"):
            Progress(value)

    @given(
        strategies.decimals(
            min_value=Decimal("1.001"),
            max_value=Decimal("1000"),
            allow_nan=False,
            allow_infinity=False,
        )
    )
    def test_over_one_always_rejected(self, value: Decimal) -> None:
        """Any value over 1 raises ValueError."""
        with pytest.raises(ValueError, match="between 0 and 1"):
            Progress(value)

    @given(
        strategies.decimals(
            min_value=Decimal("0"),
            max_value=Decimal("1"),
            allow_nan=False,
            allow_infinity=False,
            places=4,
        )
    )
    def test_valid_range_always_accepted(self, value: Decimal) -> None:
        """Any value in [0, 1] constructs successfully."""
        p = Progress(value)
        assert p.value == value
