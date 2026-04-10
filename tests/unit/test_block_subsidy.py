"""Tests for block subsidy calculation."""

from hashbidder.domain.block_height import BlockHeight
from hashbidder.domain.block_subsidy import block_subsidy
from hashbidder.domain.sats import Sats


class TestBlockSubsidy:
    """Tests for the block_subsidy domain function."""

    def test_genesis_block(self) -> None:
        """Genesis block (height 0) yields 50 BTC."""
        assert block_subsidy(BlockHeight(0)) == Sats(50_00_000_000)

    def test_last_block_before_first_halving(self) -> None:
        """Block 209999 still yields 50 BTC."""
        assert block_subsidy(BlockHeight(209_999)) == Sats(50_00_000_000)

    def test_second_halving(self) -> None:
        """Block 420000 halves subsidy to 12.5 BTC."""
        assert block_subsidy(BlockHeight(420_000)) == Sats(12_50_000_000)

    def test_far_future_zero_subsidy(self) -> None:
        """After 64 halvings the subsidy is zero."""
        assert block_subsidy(BlockHeight(210_000 * 64)) == Sats(0)
