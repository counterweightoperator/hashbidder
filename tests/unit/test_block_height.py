"""Tests for the BlockHeight domain primitive."""

import pytest

from hashbidder.domain.block_height import BlockHeight


class TestBlockHeight:
    """Tests for BlockHeight construction and validation."""

    def test_genesis(self) -> None:
        """Height 0 (genesis) is valid."""
        assert BlockHeight(0).value == 0

    def test_positive(self) -> None:
        """Positive heights are valid."""
        assert BlockHeight(840_000).value == 840_000

    def test_negative_raises(self) -> None:
        """Negative height is rejected."""
        with pytest.raises(ValueError, match="non-negative"):
            BlockHeight(-1)

    def test_equality(self) -> None:
        """Same value means equal."""
        assert BlockHeight(100) == BlockHeight(100)

    def test_inequality(self) -> None:
        """Different values are not equal."""
        assert BlockHeight(100) != BlockHeight(200)

    def test_str(self) -> None:
        """String representation is the raw integer."""
        assert str(BlockHeight(42)) == "42"
