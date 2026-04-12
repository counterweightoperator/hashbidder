"""Target-hashrate set-bids use case (no cooldown handling)."""

from dataclasses import dataclass

from hashbidder.bid_runner import SetBidsResult, reconcile
from hashbidder.client import HashpowerClient
from hashbidder.config import BidConfig, SetBidsConfig, TargetHashrateConfig
from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashratePrice
from hashbidder.ocean_client import OceanSource, OceanTimeWindow
from hashbidder.target_hashrate import (
    compute_needed_hashrate,
    distribute_bids,
    find_market_price,
)


@dataclass(frozen=True)
class TargetHashrateInputs:
    """The values that drove a target-hashrate planning run."""

    ocean_24h: Hashrate
    target: Hashrate
    needed: Hashrate
    price: HashratePrice


@dataclass(frozen=True)
class SetBidsTargetResult:
    """Result of running set_bids_target: planning inputs plus reconciliation."""

    inputs: TargetHashrateInputs
    set_bids_result: SetBidsResult


def _ocean_24h(ocean: OceanSource, address: BtcAddress) -> Hashrate:
    stats = ocean.get_account_stats(address)
    for window in stats.windows:
        if window.window is OceanTimeWindow.DAY:
            return window.hashrate
    raise ValueError("Ocean stats response did not include a 24h window")


def set_bids_target(
    client: HashpowerClient,
    ocean: OceanSource,
    address: BtcAddress,
    config: TargetHashrateConfig,
    dry_run: bool,
) -> SetBidsTargetResult:
    """Plan reconciliation to drive the 24h Ocean hashrate toward target.

    Steps:
        1. Read Ocean's 24h hashrate.
        2. Find the cheapest served bid in the order book and undercut it by 1 sat.
        3. Compute needed hashrate and split it across up to `max_bids_count` bids.
        4. Build a SetBidsConfig and hand it to the reconciliation engine.

    Cooldown handling is added in step 5 of the plan; this use case ignores it.
    """
    ocean_24h = _ocean_24h(ocean, address)
    orderbook = client.get_orderbook()
    price = find_market_price(orderbook)
    needed = compute_needed_hashrate(config.target_hashrate, ocean_24h)
    speeds = distribute_bids(needed, config.max_bids_count)

    bids = tuple(BidConfig(price=price, speed_limit=speed) for speed in speeds)
    computed = SetBidsConfig(
        default_amount=config.default_amount,
        upstream=config.upstream,
        bids=bids,
    )

    inputs = TargetHashrateInputs(
        ocean_24h=ocean_24h,
        target=config.target_hashrate,
        needed=needed,
        price=price,
    )
    return SetBidsTargetResult(
        inputs=inputs,
        set_bids_result=reconcile(client, computed, dry_run),
    )
