"""Target-hashrate set-bids use case."""

from dataclasses import dataclass
from datetime import UTC, datetime

from hashbidder.bid_runner import SetBidsResult, reconcile
from hashbidder.client import ApiError, HashpowerClient, MarketSettings, UserBid
from hashbidder.config import SetBidsConfig, TargetHashrateConfig
from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashratePrice
from hashbidder.ocean_client import OceanSource, OceanTimeWindow
from hashbidder.target_hashrate import (
    BidWithCooldown,
    compute_needed_hashrate,
    find_market_price,
    plan_with_cooldowns,
)


@dataclass(frozen=True)
class TargetHashrateInputs:
    """The values that drove a target-hashrate planning run."""

    ocean_24h: Hashrate
    target: Hashrate
    needed: Hashrate
    price: HashratePrice
    max_bids_count: int
    annotated_bids: tuple[BidWithCooldown, ...]


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


def resolve_cooldowns(
    bids: tuple[UserBid, ...],
    settings: MarketSettings,
    now: datetime,
    client: HashpowerClient,
) -> tuple[BidWithCooldown, ...]:
    """Per-bid cooldown annotation.

    Call ``get_bid_history`` and derive the authoritative answer from the bid's
    history. If the history fetch raises an ``ApiError``, fall back to a per-field
    conservative estimate: each flag is True unless its tier-1 predicate proves it
    free.
    """
    bids_with_cooldown: list[BidWithCooldown] = []
    for bid in bids:
        try:
            history = client.get_bid_history(bid.id)
        except ApiError:
            is_this_bid_in_price_cooldown = True
            is_this_bid_in_speed_cooldown = True
        else:
            last_price_decrease_at = history.last_price_decrease_at()
            is_this_bid_in_price_cooldown = (
                last_price_decrease_at is not None
                and now - last_price_decrease_at
                < settings.min_bid_price_decrease_period
            )
            last_speed_decrease_at = history.last_speed_decrease_at()
            is_this_bid_in_speed_cooldown = (
                last_speed_decrease_at is not None
                and now - last_speed_decrease_at
                < settings.min_bid_speed_limit_decrease_period
            )
        bids_with_cooldown.append(
            BidWithCooldown(
                bid=bid,
                is_price_in_cooldown=is_this_bid_in_price_cooldown,
                is_speed_in_cooldown=is_this_bid_in_speed_cooldown,
            )
        )
    return tuple(bids_with_cooldown)


def set_bids_target(
    client: HashpowerClient,
    ocean: OceanSource,
    address: BtcAddress,
    config: TargetHashrateConfig,
    dry_run: bool,
    now: datetime | None = None,
) -> SetBidsTargetResult:
    """Plan reconciliation to drive the 24h Ocean hashrate toward target.

    Steps:
        1. Read Ocean's 24h hashrate.
        2. Find the cheapest served bid in the order book and undercut it by 1 sat.
        3. Compute needed hashrate.
        4. Resolve per-bid cooldowns: fetches /spot/bid/detail history for the rest and
           derives authoritative per-field timestamps.
        5. Build a cooldown-aware SetBidsConfig and hand it to reconciliation.

    `now` defaults to the current UTC time; tests inject a fixed value.
    """
    if now is None:
        now = datetime.now(UTC)

    ocean_24h = _ocean_24h(ocean, address)
    settings = client.get_market_settings()
    orderbook = client.get_orderbook()
    price_to_set_bids_to = find_market_price(orderbook, settings.price_tick)
    total_hashrate_to_set = compute_needed_hashrate(config.target_hashrate, ocean_24h)

    current_bids = client.get_current_bids()
    bids_with_cooldowns = resolve_cooldowns(current_bids, settings, now, client)
    target_bid_states = plan_with_cooldowns(
        desired_price=price_to_set_bids_to,
        hashrate_to_set=total_hashrate_to_set,
        max_bids_count=config.max_bids_count,
        bids_with_cooldowns=bids_with_cooldowns,
    )

    computed = SetBidsConfig(
        default_amount=config.default_amount,
        upstream=config.upstream,
        bids=target_bid_states,
    )

    inputs = TargetHashrateInputs(
        ocean_24h=ocean_24h,
        target=config.target_hashrate,
        needed=total_hashrate_to_set,
        price=price_to_set_bids_to,
        max_bids_count=config.max_bids_count,
        annotated_bids=bids_with_cooldowns,
    )

    set_bids_result = reconcile(client, computed, dry_run)

    return SetBidsTargetResult(
        inputs=inputs,
        set_bids_result=set_bids_result,
    )
