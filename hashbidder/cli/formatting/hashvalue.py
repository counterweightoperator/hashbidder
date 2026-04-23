"""Formatters for the hashvalue command."""

import httpx

from hashbidder.domain.hashvalue import HashvalueComponents


def format_hashvalue(components: HashvalueComponents) -> str:
    """Format hashvalue as a single line."""
    return f"Hashvalue: {components.hashvalue.sats} sat/PH/Day"


def format_hashvalue_verbose(
    components: HashvalueComponents, mempool_url: httpx.URL
) -> str:
    """Format hashvalue with all intermediate components."""
    lines = [
        format_hashvalue(components),
        "",
        f"  Tip height:       {components.tip_height}",
        f"  Block subsidy:    {components.subsidy} sat",
        f"  Total fees (2016): {components.total_fees} sat",
        f"  Total reward (2016): {components.total_reward} sat",
        f"  Difficulty:       {components.difficulty}",
        f"  Network hashrate: {components.network_hashrate:.2E} H/s",
        f"  Mempool instance: {mempool_url}",
    ]
    return "\n".join(lines)
