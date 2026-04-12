"""Tests for hashvalue formatting functions."""

from decimal import Decimal

import httpx

from hashbidder.domain.block_height import BlockHeight
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.formatting import format_hashvalue, format_hashvalue_verbose
from hashbidder.hashvalue import HashvalueComponents

COMPONENTS = HashvalueComponents(
    tip_height=BlockHeight(840_000),
    subsidy=Sats(312_500_000),
    total_fees=Sats(50_000_000_000),
    total_reward=Sats(680_000_000_000),
    difficulty=Decimal("100_000_000_000"),
    network_hashrate=Decimal("7.158E+17"),
    hashvalue=HashratePrice(
        sats=Sats(67_853_502),
        per=Hashrate(Decimal(1), HashUnit.PH, TimeUnit.DAY),
    ),
)


class TestFormatHashvalue:
    """Tests for format_hashvalue."""

    def test_default_output(self) -> None:
        """Single line with hashvalue in sat/PH/Day."""
        assert format_hashvalue(COMPONENTS) == "Hashvalue: 67853502 sat/PH/Day"


class TestFormatHashvalueVerbose:
    """Tests for format_hashvalue_verbose."""

    def test_includes_all_components(self) -> None:
        """Verbose output includes all intermediate values."""
        output = format_hashvalue_verbose(
            COMPONENTS, httpx.URL("https://mempool.example.com")
        )

        assert "67853502 sat/PH/Day" in output
        assert "840000" in output
        assert "312500000" in output
        assert "50000000000" in output
        assert "680000000000" in output
        assert "100000000000" in output
        assert "H/s" in output
        assert "https://mempool.example.com" in output
