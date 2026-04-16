"""Tests for the bidding-strategy dispatcher."""

from decimal import Decimal

from hashbidder.domain.bid_config import BidConfig
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.strategies import BiddingStrategyKind, resolve_strategy
from hashbidder.strategies.naive import plan as naive_plan
from hashbidder.strategies.prioritized_buckets import plan as buckets_plan

PH_DAY = Hashrate(Decimal(1), HashUnit.PH, TimeUnit.DAY)
TARGET = HashratePrice(sats=Sats(500), per=PH_DAY)


def _ph_s(value: str) -> Hashrate:
    return Hashrate(Decimal(value), HashUnit.PH, TimeUnit.SECOND)


class TestResolveStrategy:
    """Tests for resolve_strategy."""

    def test_naive_resolves_to_naive_plan(self) -> None:
        """NAIVE maps to hashbidder.strategies.naive.plan."""
        assert resolve_strategy(BiddingStrategyKind.NAIVE) is naive_plan

    def test_prioritized_buckets_resolves_to_buckets_plan(self) -> None:
        """PRIORITIZED_BUCKETS maps to the buckets plan function."""
        assert resolve_strategy(BiddingStrategyKind.PRIORITIZED_BUCKETS) is buckets_plan

    def test_both_strategies_accept_the_protocol_signature(self) -> None:
        """Both resolved strategies run against the shared Protocol signature."""
        for kind in BiddingStrategyKind:
            strategy = resolve_strategy(kind)
            result = strategy(
                target_price=TARGET,
                target_hashrate=_ph_s("5"),
                max_bids_count=3,
                bids=(),
            )
            assert isinstance(result, tuple)
            assert all(isinstance(c, BidConfig) for c in result)
