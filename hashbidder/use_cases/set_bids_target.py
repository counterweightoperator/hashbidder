"""Target-hashrate set-bids use case."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from hashbidder.bid_runner import SetBidsResult, execute_plan
from hashbidder.client import ApiError, HashpowerClient, MarketSettings, UserBid
from hashbidder.config import TargetHashrateConfig
from hashbidder.domain.balance_check import check_balance
from hashbidder.domain.bid_config import BidConfig
from hashbidder.domain.bid_planning import (
    MANAGEABLE_STATUSES,
    CancelAction,
    CancelReason,
    CreateAction,
    EditAction,
    ReconciliationPlan,
)
from hashbidder.domain.btc_address import BtcAddress
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.ocean_client import OceanSource, OceanTimeWindow
from hashbidder.target_hashrate import (
    BidWithCooldown,
    compute_needed_hashrate,
    find_market_price,
)


@dataclass(frozen=True)
class TargetHashrateInputs:
    """The values that drove a target-hashrate planning run."""

    ocean_24h: Hashrate
    target: Hashrate
    needed: Hashrate
    price: HashratePrice
    bids_with_cooldowns: tuple[BidWithCooldown, ...]


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
    conservative estimate: each flag is True.
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
        4. Resolve per-bid cooldowns: fetches /spot/bid/detail history and derives
           authoritative per-field timestamps.
        5. Converge on a single bid: cancel extras (keeping the most flexible
           and largest), create one if none exists, or edit/skip the survivor
           subject to cooldown constraints.
        6. Check the account balance and, unless `dry_run`, execute the plan.

    `now` defaults to the current UTC time; tests inject a fixed value.
    """
    if now is None:
        now = datetime.now(UTC)

    ocean_24h = _ocean_24h(ocean, address)
    settings = client.get_market_settings()
    orderbook = client.get_orderbook()
    price_to_set_bids_to = find_market_price(orderbook, settings.price_tick)
    total_hashrate_to_set = compute_needed_hashrate(config.target_hashrate, ocean_24h)
    we_need_no_hashrate = total_hashrate_to_set == Hashrate(
        value=Decimal(0), hash_unit=HashUnit.PH, time_unit=TimeUnit.SECOND
    )

    current_bids = client.get_current_bids()
    manageable_bids = tuple(b for b in current_bids if b.status in MANAGEABLE_STATUSES)
    bids_with_cooldowns = resolve_cooldowns(manageable_bids, settings, now, client)

    cancel_actions: tuple[CancelAction, ...] = ()
    create_actions: tuple[CreateAction, ...] = ()
    edit_actions: tuple[EditAction, ...] = ()
    skipped_bids: tuple[UserBid, ...] = ()

    if len(bids_with_cooldowns) > 1:
        if we_need_no_hashrate:
            cancel_actions = tuple(
                CancelAction(bid=b.bid, reason=CancelReason.NEED_ZERO_HASHRATE)
                for b in bids_with_cooldowns
            )

        def keep_most_flexible_largest_bid(b: BidWithCooldown) -> tuple[int, int]:
            locks = int(b.is_price_in_cooldown) + int(b.is_speed_in_cooldown)
            remaining = b.bid.amount_remaining_sat
            if remaining is None:
                remaining = b.bid.amount_sat
            return (locks, -remaining)

        kept_bid = min(bids_with_cooldowns, key=keep_most_flexible_largest_bid)

        cancel_actions = tuple(
            CancelAction(bid=b.bid, reason=CancelReason.TOO_MANY_BIDS)
            for b in bids_with_cooldowns
            if b is not kept_bid
        )
        remaining_bids: tuple[BidWithCooldown, ...] = (kept_bid,)
    else:
        remaining_bids = bids_with_cooldowns

    if len(remaining_bids) < 1 and not we_need_no_hashrate:
        create_actions = (
            CreateAction(
                config=BidConfig(
                    price=price_to_set_bids_to,
                    speed_limit=total_hashrate_to_set,
                ),
                amount=config.default_amount,
                upstream=config.upstream,
            ),
        )

    if len(remaining_bids) == 1:
        the_bid = remaining_bids[0]
        is_fully_flexible = (
            not the_bid.is_price_in_cooldown and not the_bid.is_speed_in_cooldown
        )
        is_fully_locked = the_bid.is_price_in_cooldown and the_bid.is_speed_in_cooldown
        desired_price_change_is_increase = the_bid.bid.price < price_to_set_bids_to
        desired_speed_change_is_increase = (
            the_bid.bid.speed_limit_ph < total_hashrate_to_set
        )
        the_bid_is_price_aligned = the_bid.bid.price == price_to_set_bids_to
        the_bid_is_speed_aligned = the_bid.bid.speed_limit_ph == total_hashrate_to_set

        if is_fully_flexible or (
            desired_price_change_is_increase and desired_speed_change_is_increase
        ):
            if the_bid_is_price_aligned and the_bid_is_speed_aligned:
                skipped_bids = (the_bid.bid,)
            else:
                edit_actions = (
                    EditAction(
                        bid=the_bid.bid,
                        new_price=price_to_set_bids_to,
                        new_speed_limit_ph=total_hashrate_to_set,
                    ),
                )
        elif is_fully_locked:
            skipped_bids = (the_bid.bid,)
        elif the_bid.is_price_in_cooldown:
            if the_bid_is_speed_aligned:
                skipped_bids = (the_bid.bid,)
            else:
                edit_actions = (
                    EditAction(
                        bid=the_bid.bid,
                        new_price=the_bid.bid.price,
                        new_speed_limit_ph=total_hashrate_to_set,
                    ),
                )
        elif the_bid.is_speed_in_cooldown:
            if the_bid_is_price_aligned:
                skipped_bids = (the_bid.bid,)
            else:
                edit_actions = (
                    EditAction(
                        bid=the_bid.bid,
                        new_price=price_to_set_bids_to,
                        new_speed_limit_ph=the_bid.bid.speed_limit_ph,
                    ),
                )

    plan = ReconciliationPlan(
        cancels=cancel_actions,
        edits=edit_actions,
        creates=create_actions,
        unchanged=skipped_bids,
    )

    available_balance = client.get_account_balance()
    balance_check = check_balance(
        plan=plan,
        available_sats=available_balance.available_sat,
    )
    if dry_run:
        set_bids_result = SetBidsResult(
            plan=plan,
            skipped_bids=(),
            balance_check=balance_check,
        )
    else:
        execution = execute_plan(client, plan)
        set_bids_result = SetBidsResult(
            plan=plan,
            skipped_bids=(),
            balance_check=balance_check,
            execution=execution,
        )

    inputs = TargetHashrateInputs(
        ocean_24h=ocean_24h,
        target=config.target_hashrate,
        needed=total_hashrate_to_set,
        price=price_to_set_bids_to,
        bids_with_cooldowns=bids_with_cooldowns,
    )

    return SetBidsTargetResult(
        inputs=inputs,
        set_bids_result=set_bids_result,
    )
