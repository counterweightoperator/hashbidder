"""Tests for hashvalue computation."""

from decimal import Decimal

import pytest

from hashbidder.domain.block_height import BlockHeight
from hashbidder.domain.sats import Sats
from hashbidder.hashvalue import compute_hashvalue


class TestComputeHashvalue:
    """Tests for the compute_hashvalue pure function."""

    def test_hand_computed(self) -> None:
        """Verify against a hand-computed example.

        difficulty = 100_000_000_000 (1e11)
        tip_height = 840_000  (subsidy = 3.125 BTC = 312_500_000 sat)
        total_fees = 50_000_000_000 sat (50 BTC over 2016 blocks)

        total_reward = 2016 * 312_500_000 + 50_000_000_000 = 680_000_000_000
        avg_reward   = 680_000_000_000 / 2016 ≈ 337_301_587.30
        network_hr   = 1e11 * 2^32 / 600 ≈ 7.158e17 H/s
        hashvalue    = 337_301_587.30 * 144 * 1e15 / 7.158e17 ≈ 67_853_502
        """
        result = compute_hashvalue(
            difficulty=Decimal("100_000_000_000"),
            tip_height=BlockHeight(840_000),
            total_fees=Sats(50_000_000_000),
        )
        assert result.subsidy == Sats(312_500_000)
        assert result.total_reward == 2016 * 312_500_000 + 50_000_000_000
        assert result.hashvalue.sats == 67_853_502

    def test_different_heights_give_different_subsidies(self) -> None:
        """Passing different heights changes the subsidy and thus the result."""
        r1 = compute_hashvalue(
            difficulty=Decimal("1e11"),
            tip_height=BlockHeight(210_000),
            total_fees=Sats(0),
        )
        r2 = compute_hashvalue(
            difficulty=Decimal("1e11"),
            tip_height=BlockHeight(420_000),
            total_fees=Sats(0),
        )
        assert r1.subsidy == Sats(25_00_000_000)
        assert r2.subsidy == Sats(12_50_000_000)
        assert r1.hashvalue.sats > r2.hashvalue.sats

    def test_zero_fees(self) -> None:
        """With zero fees, hashvalue is subsidy-only."""
        result = compute_hashvalue(
            difficulty=Decimal("1e11"),
            tip_height=BlockHeight(840_000),
            total_fees=Sats(0),
        )
        assert result.total_fees == 0
        assert result.total_reward == 2016 * 312_500_000
        assert result.hashvalue.sats > 0

    def test_zero_difficulty_raises(self) -> None:
        """Zero difficulty causes a ZeroDivisionError."""
        with pytest.raises(ZeroDivisionError):
            compute_hashvalue(
                difficulty=Decimal("0"),
                tip_height=BlockHeight(840_000),
                total_fees=Sats(0),
            )
