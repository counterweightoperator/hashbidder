"""Hashvalue computation — expected sats earned per PH/day from on-chain data."""

from dataclasses import dataclass
from decimal import Decimal

from hashbidder.domain.block_height import BlockHeight
from hashbidder.domain.block_subsidy import block_subsidy
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit

BLOCKS_PER_EPOCH = 2016
BLOCKS_PER_DAY = 144
BLOCK_TIME_SECONDS = 600


@dataclass(frozen=True)
class HashvalueComponents:
    """All intermediates and the final hashvalue result."""

    tip_height: BlockHeight
    subsidy: Sats
    total_fees: Sats
    total_reward: Sats
    difficulty: Decimal
    network_hashrate: Decimal
    hashvalue: HashratePrice


def compute_hashvalue(
    difficulty: Decimal,
    tip_height: BlockHeight,
    total_fees: Sats,
) -> HashvalueComponents:
    """Compute hashvalue (sat/PH/day) from on-chain parameters.

    Raises ZeroDivisionError if difficulty is zero.
    """
    subsidy = block_subsidy(tip_height)
    total_reward = Sats(BLOCKS_PER_EPOCH * subsidy + total_fees)
    avg_reward = Decimal(total_reward) / BLOCKS_PER_EPOCH
    network_hashrate = difficulty * Decimal(2**32) / BLOCK_TIME_SECONDS
    hashvalue = (
        avg_reward * BLOCKS_PER_DAY * Decimal(HashUnit.PH.value) / network_hashrate
    )

    return HashvalueComponents(
        tip_height=tip_height,
        subsidy=subsidy,
        total_fees=total_fees,
        total_reward=total_reward,
        difficulty=difficulty,
        network_hashrate=network_hashrate,
        hashvalue=HashratePrice(
            sats=Sats(round(hashvalue)),
            per=Hashrate(Decimal(1), HashUnit.PH, TimeUnit.DAY),
        ),
    )
