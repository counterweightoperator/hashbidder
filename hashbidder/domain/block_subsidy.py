"""Block subsidy derived from height using Bitcoin's halving schedule."""

from hashbidder.domain.block_height import BlockHeight
from hashbidder.domain.sats import Sats

INITIAL_SUBSIDY = 5_000_000_000
HALVING_INTERVAL = 210_000


def block_subsidy(height: BlockHeight) -> Sats:
    """Return the block subsidy in satoshis for the given block height."""
    return Sats(INITIAL_SUBSIDY >> (height.value // HALVING_INTERVAL))
