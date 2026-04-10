"""Tests for the get_hashvalue use case."""

from decimal import Decimal

import pytest

from hashbidder.domain.block_height import BlockHeight
from hashbidder.domain.sats import Sats
from hashbidder.mempool_client import BlockTipInfo, MempoolError, RewardStats
from hashbidder.use_cases import get_hashvalue
from tests.conftest import FakeMempoolSource


class TestGetHashvalue:
    """Tests for the get_hashvalue use case."""

    def test_happy_path(self) -> None:
        """Returns expected components from canned mempool data."""
        source = FakeMempoolSource(
            tip=BlockTipInfo(
                height=BlockHeight(840_000),
                difficulty=Decimal("100_000_000_000"),
            ),
            reward_stats=RewardStats(total_fee=Sats(50_000_000_000)),
        )

        result = get_hashvalue(source)

        assert result.tip_height == BlockHeight(840_000)
        assert result.subsidy == Sats(312_500_000)
        assert result.total_fees == Sats(50_000_000_000)
        assert result.hashvalue.sats == 67_853_502

    def test_error_propagates(self) -> None:
        """MempoolError from source propagates to caller."""
        source = FakeMempoolSource(
            tip=BlockTipInfo(
                height=BlockHeight(0),
                difficulty=Decimal("1"),
            ),
            reward_stats=RewardStats(total_fee=Sats(0)),
            error=MempoolError(503, "service unavailable"),
        )

        with pytest.raises(MempoolError) as exc_info:
            get_hashvalue(source)
        assert exc_info.value.status_code == 503
