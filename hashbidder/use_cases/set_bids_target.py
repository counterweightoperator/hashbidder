"""Target-hashrate set-bids use case."""

from dataclasses import dataclass
from datetime import UTC, datetime

from hashbidder.bid_runner import SetBidsResult, reconcile
from hashbidder.client import ApiError, HashpowerClient, MarketSettings, UserBid
from hashbidder.config import SetBidsConfig, TargetHashrateConfig
from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashratePrice
from hashbidder.ocean_client import OceanSource, OceanTimeWindow
from hashbidder.strategies import BiddingStrategyKind, resolve_strategy
from hashbidder.target_hashrate import (
    BidWithCooldown,
    CooldownInfo,
    compute_needed_hashrate,
    cooldown_from_history,
    find_market_price,
    is_price_guaranteed_free,
    is_speed_guaranteed_free,
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
    """Per-bid cooldown annotation via a cheap tier-1 check, then tier-2 history.

    Per bid, in order:

    1. **Cheap check.** If the tier-1 predicates prove both fields past
       their decrease windows (i.e. ``last_updated`` is old enough),
       emit ``CooldownInfo(False, False)`` — no history fetch needed.
    2. **History fetch.** Otherwise, call ``get_bid_history`` and derive
       the authoritative answer from the bid's history.
    3. **Fetch failure fallback.** If the history fetch raises an
       ``ApiError``, fall back to a per-field conservative estimate:
       each flag is True unless its tier-1 predicate proves it free.
    """
    annotated: list[BidWithCooldown] = []
    for bid in bids:
        price_free = is_price_guaranteed_free(bid, settings, now)
        speed_free = is_speed_guaranteed_free(bid, settings, now)
        if price_free and speed_free:
            cooldown = CooldownInfo(price_cooldown=False, speed_cooldown=False)
        else:
            try:
                history = client.get_bid_history(bid.id)
            except ApiError:
                cooldown = CooldownInfo(
                    price_cooldown=not price_free,
                    speed_cooldown=not speed_free,
                )
            else:
                cooldown = cooldown_from_history(history, settings, now)
        annotated.append(BidWithCooldown(bid=bid, cooldown=cooldown))
    return tuple(annotated)


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
        4. Resolve per-bid cooldowns: tier-1 cheap predicates clear bids
           that are provably not-in-cooldown with zero extra calls; tier-2
           fetches /spot/bid/detail history for the rest and derives
           authoritative per-field timestamps.
        5. Build a cooldown-aware SetBidsConfig and hand it to reconciliation.

    `now` defaults to the current UTC time; tests inject a fixed value.
    """
    if now is None:
        now = datetime.now(UTC)

    ocean_24h = _ocean_24h(ocean, address)
    settings = client.get_market_settings()
    orderbook = client.get_orderbook()
    price = find_market_price(orderbook, settings.price_tick)
    needed = compute_needed_hashrate(config.target_hashrate, ocean_24h)

    current_bids = client.get_current_bids()
    annotated = resolve_cooldowns(current_bids, settings, now, client)
    strategy = resolve_strategy(BiddingStrategyKind.NAIVE)
    bids = strategy(
        target_price=price,
        target_hashrate=needed,
        max_bids_count=config.max_bids_count,
        bids=annotated,
    )
    for entry in bids:
        settings.price_tick.assert_aligned(entry.price)

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
        max_bids_count=config.max_bids_count,
        annotated_bids=annotated,
    )
    return SetBidsTargetResult(
        inputs=inputs,
        set_bids_result=reconcile(client, computed, dry_run),
    )
