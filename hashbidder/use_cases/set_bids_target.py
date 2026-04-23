"""Target-hashrate set-bids use case."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from hashbidder.clients.braiins import (
    AccountBalance,
    ApiError,
    HashpowerClient,
    MarketSettings,
    UserBid,
)
from hashbidder.clients.ocean import OceanSource, OceanTimeWindow
from hashbidder.domain.balance_check import check_balance
from hashbidder.domain.bid_config import BidConfig, TargetHashrateConfig
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
from hashbidder.domain.target_hashrate import compute_needed_hashrate
from hashbidder.domain.time_unit import TimeUnit
from hashbidder.services.bid_runner import ExecutionResult, SetBidsResult, execute_plan
from hashbidder.services.target_hashrate import BidWithCooldown, find_market_price


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


def _keep_most_flexible_largest_bid(b: BidWithCooldown) -> tuple[int, int]:
    locks = int(b.is_price_in_cooldown) + int(b.is_speed_in_cooldown)
    remaining = b.bid.amount_remaining_sat
    if remaining is None:
        remaining = b.bid.amount_sat
    return (locks, -remaining)


@dataclass(frozen=True)
class _GatheredInputs:
    inputs: TargetHashrateInputs
    non_manageable_bids: tuple[UserBid, ...]
    available_balance: AccountBalance


def _gather_inputs(
    client: HashpowerClient,
    ocean: OceanSource,
    address: BtcAddress,
    config: TargetHashrateConfig,
    now: datetime,
) -> _GatheredInputs:
    ocean_24h = _ocean_24h(ocean, address)
    settings = client.get_market_settings()
    orderbook = client.get_orderbook()
    price = find_market_price(orderbook, settings.price_tick)
    needed = compute_needed_hashrate(config.target_hashrate, ocean_24h)

    current_bids = client.get_current_bids()
    manageable_bids = tuple(b for b in current_bids if b.status in MANAGEABLE_STATUSES)
    non_manageable_bids = tuple(
        b for b in current_bids if b.status not in MANAGEABLE_STATUSES
    )
    bids_with_cooldowns = resolve_cooldowns(manageable_bids, settings, now, client)

    return _GatheredInputs(
        inputs=TargetHashrateInputs(
            ocean_24h=ocean_24h,
            target=config.target_hashrate,
            needed=needed,
            price=price,
            bids_with_cooldowns=bids_with_cooldowns,
        ),
        non_manageable_bids=non_manageable_bids,
        available_balance=client.get_account_balance(),
    )


def _plan_reconciliation(
    inputs: TargetHashrateInputs,
    config: TargetHashrateConfig,
) -> ReconciliationPlan:
    we_need_no_hashrate = inputs.needed == Hashrate(
        value=Decimal(0), hash_unit=HashUnit.PH, time_unit=TimeUnit.SECOND
    )

    cancel_actions: tuple[CancelAction, ...] = ()
    create_actions: tuple[CreateAction, ...] = ()
    edit_actions: tuple[EditAction, ...] = ()
    unchanged_bids: tuple[UserBid, ...] = ()

    if we_need_no_hashrate:
        cancel_actions = tuple(
            CancelAction(bid=b.bid, reason=CancelReason.NEED_ZERO_HASHRATE)
            for b in inputs.bids_with_cooldowns
        )
    elif not inputs.bids_with_cooldowns:
        create_actions = (
            CreateAction(
                config=BidConfig(
                    price=inputs.price,
                    speed_limit=inputs.needed,
                ),
                amount=config.default_amount,
                upstream=config.upstream,
            ),
        )
    else:
        kept_bid = min(inputs.bids_with_cooldowns, key=_keep_most_flexible_largest_bid)
        cancel_actions = tuple(
            CancelAction(bid=b.bid, reason=CancelReason.TOO_MANY_BIDS)
            for b in inputs.bids_with_cooldowns
            if b is not kept_bid
        )
        new_price = (
            kept_bid.bid.price
            if kept_bid.is_price_in_cooldown and inputs.price < kept_bid.bid.price
            else inputs.price
        )
        new_speed = (
            kept_bid.bid.speed_limit_ph
            if kept_bid.is_speed_in_cooldown
            and inputs.needed < kept_bid.bid.speed_limit_ph
            else inputs.needed
        )
        if new_price == kept_bid.bid.price and new_speed == kept_bid.bid.speed_limit_ph:
            unchanged_bids = (kept_bid.bid,)
        else:
            edit_actions = (
                EditAction(
                    bid=kept_bid.bid,
                    new_price=new_price,
                    new_speed_limit_ph=new_speed,
                ),
            )

    return ReconciliationPlan(
        cancels=cancel_actions,
        edits=edit_actions,
        creates=create_actions,
        unchanged=unchanged_bids,
    )


def _apply_plan(
    client: HashpowerClient,
    plan: ReconciliationPlan,
    dry_run: bool,
) -> ExecutionResult | None:
    if dry_run:
        return None
    return execute_plan(client, plan)


def set_bids_target(
    client: HashpowerClient,
    ocean: OceanSource,
    address: BtcAddress,
    config: TargetHashrateConfig,
    dry_run: bool,
    now: datetime | None = None,
) -> SetBidsTargetResult:
    """Plan reconciliation to drive the 24h Ocean hashrate toward target.

    Three phases:
        1. Gather inputs: read Ocean 24h, market settings, orderbook, current
           bids (with per-bid cooldown annotations), and the account balance.
        2. Plan: reconcile toward a single target bid. Cancel all if needed
           hashrate is zero; create one if no bids exist; else keep the most
           flexible/largest bid, cancel the rest, and edit/skip the keeper.
           Cooldowns block decreases only; increases always go through.
        3. Apply: unless `dry_run`, execute the plan.

    `now` defaults to the current UTC time; tests inject a fixed value.
    """
    if now is None:
        now = datetime.now(UTC)

    gathered = _gather_inputs(client, ocean, address, config, now)
    plan = _plan_reconciliation(gathered.inputs, config)
    balance_check = check_balance(
        plan=plan,
        available_sats=gathered.available_balance.available_sat,
    )
    execution_result = _apply_plan(client, plan, dry_run)

    return SetBidsTargetResult(
        inputs=gathered.inputs,
        set_bids_result=SetBidsResult(
            plan=plan,
            skipped_bids=gathered.non_manageable_bids,
            balance_check=balance_check,
            execution=execution_result,
        ),
    )
