"""CLI tests for the hashvalue command."""

from decimal import Decimal

from click.testing import CliRunner

from hashbidder.domain.block_height import BlockHeight
from hashbidder.domain.sats import Sats
from hashbidder.main import Clients, cli
from hashbidder.mempool_client import ChainStats, MempoolError
from tests.conftest import FakeMempoolSource

CHAIN_STATS = ChainStats(
    tip_height=BlockHeight(840_000),
    difficulty=Decimal("100_000_000_000"),
    total_fee=Sats(50_000_000_000),
)


def test_hashvalue_prints_result() -> None:
    """Hashvalue command prints a single line with the value."""
    source = FakeMempoolSource(chain_stats=CHAIN_STATS)
    runner = CliRunner()
    result = runner.invoke(cli, ["hashvalue"], obj=Clients(mempool=source))

    assert result.exit_code == 0
    assert "67853502 sat/PH/Day" in result.output


def test_hashvalue_verbose() -> None:
    """Verbose flag includes intermediate components."""
    source = FakeMempoolSource(chain_stats=CHAIN_STATS)
    runner = CliRunner()
    result = runner.invoke(cli, ["-v", "hashvalue"], obj=Clients(mempool=source))

    assert result.exit_code == 0
    assert "840000" in result.output
    assert "312500000" in result.output
    assert "Mempool instance" in result.output


def test_hashvalue_error() -> None:
    """Mempool error results in non-zero exit code."""
    source = FakeMempoolSource(
        chain_stats=CHAIN_STATS,
        error=MempoolError(503, "service unavailable"),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["hashvalue"], obj=Clients(mempool=source))

    assert result.exit_code != 0
    assert "service unavailable" in result.output
