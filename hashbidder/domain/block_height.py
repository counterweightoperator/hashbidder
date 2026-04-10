"""Block height primitive."""


class BlockHeight:
    """A non-negative Bitcoin block height, starting at genesis (0)."""

    def __init__(self, value: int) -> None:
        """Create a block height, raising ValueError if negative."""
        if value < 0:
            raise ValueError(f"Block height must be non-negative, got {value}")
        self._value = value

    @property
    def value(self) -> int:
        """The raw integer height."""
        return self._value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, BlockHeight):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    def __repr__(self) -> str:
        return f"BlockHeight({self._value})"

    def __str__(self) -> str:
        return str(self._value)
