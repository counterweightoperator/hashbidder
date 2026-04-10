"""Block subsidy derived from height using Bitcoin's halving schedule."""

from hashbidder.domain.bitcoin import HALVING_INTERVAL, INITIAL_SUBSIDY
from hashbidder.domain.block_height import BlockHeight
from hashbidder.domain.sats import Sats


def block_subsidy(height: BlockHeight) -> Sats:
    """Return the block subsidy in satoshis for the given block height."""
    return Sats(INITIAL_SUBSIDY >> (height.value // HALVING_INTERVAL))
