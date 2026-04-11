"""Hashvalue use case."""

from hashbidder.domain.bitcoin import BLOCKS_PER_EPOCH
from hashbidder.hashvalue import HashvalueComponents, compute_hashvalue
from hashbidder.mempool_client import MempoolSource


def get_hashvalue(mempool: MempoolSource) -> HashvalueComponents:
    """Compute the current hashvalue from on-chain data.

    Args:
        mempool: The mempool data source to use.

    Returns:
        All intermediate components and the final hashvalue.
    """
    stats = mempool.get_chain_stats(BLOCKS_PER_EPOCH)
    return compute_hashvalue(
        difficulty=stats.difficulty,
        tip_height=stats.tip_height,
        total_fees=stats.total_fee,
    )
