"""Progress ratio primitive."""

from decimal import Decimal

_HUNDRED = Decimal("100")


class Progress:
    """A progress ratio clamped to 0..1."""

    def __init__(self, value: Decimal) -> None:
        """Initialize a progress ratio.

        Args:
            value: Ratio between 0 and 1 inclusive.

        Raises:
            ValueError: If value is outside [0, 1].
        """
        if value < 0 or value > 1:
            raise ValueError(f"Progress must be between 0 and 1, got {value}")
        self._value = value

    @classmethod
    def from_percentage(cls, pct: Decimal) -> "Progress":
        """Create a Progress from a percentage value (0..100).

        Args:
            pct: Percentage between 0 and 100 inclusive.

        Returns:
            The equivalent Progress ratio.
        """
        return cls(pct / _HUNDRED)

    @property
    def value(self) -> Decimal:
        """The raw ratio value (0..1)."""
        return self._value

    @property
    def percentage(self) -> Decimal:
        """The value as a percentage (0..100)."""
        return self._value * _HUNDRED

    def __str__(self) -> str:
        return f"{self.percentage.normalize()}%"

    def __repr__(self) -> str:
        return f"Progress({self._value!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Progress):
            return self._value == other._value
        return NotImplemented
