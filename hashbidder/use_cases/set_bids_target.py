"""Target-hashrate set-bids use case."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from itertools import product

from hashbidder.clients.braiins import (
    AccountBalance,
    ApiError,
    HashpowerClient,
    MarketSettings,
    UserBid,
)
from hashbidder.clients.ocean import OceanSource, OceanTimeWindow
from hashbidder.domain.balance_check import check_balance
from hashbidder.domain.bid_config import (
    MIN_BID_SPEED_LIMIT,
    BidConfig,
    TargetHashrateConfig,
)
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

# Action-space constants. Source-of-truth for the documented per-bid edit and
# new-bid sizing options enumerated by ``craft_all_possible_plans``.
_SPEED_INCREASE_PERCENTS: tuple[int, ...] = (10, 20, 50, 100, 200, 500, 1000)
_SPEED_DECREASE_PERCENTS: tuple[int, ...] = (10, 20, 30, 40, 50, 60, 70, 80, 90)
_NEW_BID_TARGET_FRACTIONS_PERCENT: tuple[int, ...] = (
    10,
    20,
    30,
    40,
    50,
    60,
    70,
    80,
    90,
    100,
)

# Hard cap on the number of live bids in any selectable plan. Plans with more
# than this many bids are disqualified from selection regardless of their
# other scoring components.
_HARD_CAP_BIDS = 3


@dataclass(frozen=True)
class TargetHashrateInputs:
    """The values read from upstream sources for a target-hashrate run."""

    ocean_24h: Hashrate
    target: Hashrate
    needed_hashrate: Hashrate
    target_price: HashratePrice
    bids_with_cooldowns: tuple[BidWithCooldown, ...]
    non_manageable_bids: tuple[UserBid, ...]
    available_balance: AccountBalance


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


def _gather_inputs(
    client: HashpowerClient,
    ocean: OceanSource,
    address: BtcAddress,
    config: TargetHashrateConfig,
    now: datetime,
) -> TargetHashrateInputs:
    ocean_24h = _ocean_24h(ocean, address)
    settings = client.get_market_settings()
    orderbook = client.get_orderbook()
    price = find_market_price(orderbook, settings.price_tick)
    needed_hashrate = compute_needed_hashrate(config.target_hashrate, ocean_24h)

    current_bids = client.get_current_bids()
    available_balance = client.get_account_balance()
    manageable_bids = tuple(b for b in current_bids if b.status in MANAGEABLE_STATUSES)
    non_manageable_bids = tuple(
        b for b in current_bids if b.status not in MANAGEABLE_STATUSES
    )
    bids_with_cooldowns = resolve_cooldowns(manageable_bids, settings, now, client)

    return TargetHashrateInputs(
        ocean_24h=ocean_24h,
        target=config.target_hashrate,
        needed_hashrate=needed_hashrate,
        target_price=price,
        bids_with_cooldowns=bids_with_cooldowns,
        non_manageable_bids=non_manageable_bids,
        available_balance=available_balance,
    )


# One disposition for a single existing bid in a candidate plan: either a
# CancelAction, an EditAction, or the UserBid itself (meaning "leave it alone").
_PerBidOption = CancelAction | EditAction | UserBid


def _per_bid_price_choices(
    current: HashratePrice,
    target_price: HashratePrice,
    is_price_in_cooldown: bool,
) -> list[HashratePrice]:
    choices: list[HashratePrice] = [current]
    if target_price > current or (target_price < current and not is_price_in_cooldown):
        choices.append(target_price)
    return choices


def _per_bid_speed_choices(
    current: Hashrate,
    needed_total: Hashrate,
    is_speed_in_cooldown: bool,
) -> list[Hashrate]:
    raw: list[Hashrate] = [current]
    for p in _SPEED_INCREASE_PERCENTS:
        raw.append(current * Fraction(100 + p, 100))
    if not is_speed_in_cooldown:
        for p in _SPEED_DECREASE_PERCENTS:
            raw.append(current * Fraction(100 - p, 100))
    # Gap-closing option: drive this bid alone to the full needed hashrate.
    if needed_total != current and not (
        needed_total < current and is_speed_in_cooldown
    ):
        raw.append(needed_total)
    # Drop any candidate that would violate the bid speed-limit floor.
    valid = [h for h in raw if h >= MIN_BID_SPEED_LIMIT]
    seen: set[Hashrate] = set()
    deduped: list[Hashrate] = []
    for h in valid:
        if h in seen:
            continue
        seen.add(h)
        deduped.append(h)
    return deduped


def _get_cancel_option(bid: UserBid) -> CancelAction:
    """The single cancel option for an existing bid."""
    return CancelAction(bid=bid, reason=CancelReason.TOO_MANY_BIDS)


def _get_edit_options(
    bid_with_cooldown: BidWithCooldown,
    target_price: HashratePrice,
    needed_total: Hashrate,
) -> list[EditAction]:
    """Every edit (price/speed combo that actually changes something)."""
    bid = bid_with_cooldown.bid
    price_choices = _per_bid_price_choices(
        bid.price, target_price, bid_with_cooldown.is_price_in_cooldown
    )
    speed_choices = _per_bid_speed_choices(
        bid.speed_limit_ph, needed_total, bid_with_cooldown.is_speed_in_cooldown
    )
    edits: list[EditAction] = []
    for new_price, new_speed in product(price_choices, speed_choices):
        if new_price == bid.price and new_speed == bid.speed_limit_ph:
            continue  # this is the no-op case; handled separately as "unchanged"
        edits.append(
            EditAction(bid=bid, new_price=new_price, new_speed_limit_ph=new_speed)
        )
    return edits


def get_existing_bid_options(
    bid_with_cooldown: BidWithCooldown,
    target_price: HashratePrice,
    needed_total: Hashrate,
) -> list[_PerBidOption]:
    """Enumerate every disposition this bid can take in a candidate plan."""
    bid = bid_with_cooldown.bid
    cancel = _get_cancel_option(bid)
    edits = _get_edit_options(bid_with_cooldown, target_price, needed_total)
    leave_alone = bid
    return [cancel, *edits, leave_alone]


def _create_options(
    inputs: TargetHashrateInputs, config: TargetHashrateConfig
) -> list[tuple[CreateAction, ...]]:
    # If we don't need any more hashrate, the only create option is "no new bid".
    zero_ph = Hashrate(Decimal(0), HashUnit.PH, TimeUnit.SECOND)
    if inputs.needed_hashrate == zero_ph:
        return [()]

    speeds: set[Hashrate] = {
        inputs.target * Fraction(f, 100) for f in _NEW_BID_TARGET_FRACTIONS_PERCENT
    }
    # Add the exact gap-closing option (no-op if it's already a fraction-of-target).
    speeds.add(inputs.needed_hashrate)
    # New bids are constrained by the speed-limit floor; drop anything below.
    speeds = {s for s in speeds if s >= MIN_BID_SPEED_LIMIT}

    options: list[tuple[CreateAction, ...]] = [()]
    for speed in speeds:
        options.append(
            (
                CreateAction(
                    config=BidConfig(price=inputs.target_price, speed_limit=speed),
                    amount=config.default_amount,
                    upstream=config.upstream,
                ),
            )
        )
    return options


def craft_all_possible_plans(
    inputs: TargetHashrateInputs,
    config: TargetHashrateConfig,
) -> tuple[ReconciliationPlan, ...]:
    """Enumerate every candidate plan worth scoring.

    For each existing bid, possible dispositions are:
        - Cancel
        - Leave price as is, or set price to target (decreases gated by
          ``is_price_in_cooldown``)
        - Leave hashrate as is, increase hashrate by 10/20/50/100/200/500/1000%,
          decrease hashrate by 10/20/30/40/50/60/70/80/90% (gated by
          ``is_speed_in_cooldown``), or set hashrate to the gap-closing total
          ``needed_hashrate``.

    Plus a creation slot taking on either:
        - No new bid, or
        - One new bid at market price with hashrate equal to 10/20/.../100% of
          the long-term target, or to ``needed_hashrate`` (the exact remaining
          gap).

    The result is the cartesian product over per-bid dispositions and the
    creation slot, deduplicated. Grows multiplicatively in the number of
    existing bids.
    """
    # Step 1: build a list of per-bid option-lists, one entry per existing bid.
    options_per_bid: list[list[_PerBidOption]] = []
    for bid in inputs.bids_with_cooldowns:
        options_for_this_bid = get_existing_bid_options(
            bid, inputs.target_price, inputs.needed_hashrate
        )
        options_per_bid.append(options_for_this_bid)

    # Step 2: build every possible combination by picking one option from each
    # bid's list. We start with a single empty combination and, for each bid,
    # extend every existing combination with each of that bid's options. With
    # no existing bids, the result stays as the single empty combination.
    # Combos whose live (non-cancel) pick count already exceeds _HARD_CAP_BIDS
    # are pruned: select_best_plan would discard them anyway.
    bid_combos: list[tuple[_PerBidOption, ...]] = [()]
    for options_for_this_bid in options_per_bid:
        next_combos: list[tuple[_PerBidOption, ...]] = []
        for combo_so_far in bid_combos:
            live_so_far = sum(
                1 for o in combo_so_far if not isinstance(o, CancelAction)
            )
            for option in options_for_this_bid:
                next_live = live_so_far + (0 if isinstance(option, CancelAction) else 1)
                if next_live > _HARD_CAP_BIDS:
                    continue
                next_combos.append((*combo_so_far, option))
        bid_combos = next_combos

    create_opts = _create_options(inputs, config)

    # Step 3: for each (bid combination, create option) pair, assemble a plan.
    # Skip pairings whose total live bid count would exceed _HARD_CAP_BIDS.
    plans: list[ReconciliationPlan] = []
    for combo in bid_combos:
        cancels = tuple(o for o in combo if isinstance(o, CancelAction))
        edits = tuple(o for o in combo if isinstance(o, EditAction))
        unchanged = tuple(o for o in combo if isinstance(o, UserBid))
        live_combo_count = len(edits) + len(unchanged)
        for creates in create_opts:
            if live_combo_count + len(creates) > _HARD_CAP_BIDS:
                continue
            plans.append(
                ReconciliationPlan(
                    cancels=cancels,
                    edits=edits,
                    creates=creates,
                    unchanged=unchanged,
                )
            )
    return tuple(plans)


def select_best_plan(
    candidate_plans: tuple[ReconciliationPlan, ...],
    long_term_hashrate_target: Hashrate,
    current_hashrate_target: Hashrate,
    target_price: HashratePrice,
) -> ReconciliationPlan:
    """Pick the plan with the highest score.

    Always returns a plan. Relies on the caller to provide at least one
    candidate that satisfies the hard cap on bid count
    (``craft_all_possible_plans`` guarantees this by always emitting the
    all-cancel + no-create combination, which is a 0-bid plan).
    """
    if not candidate_plans:
        raise ValueError("select_best_plan requires at least one candidate plan")

    current_target_ph = current_hashrate_target.to(HashUnit.PH, TimeUnit.SECOND).value
    long_term_ph = long_term_hashrate_target.to(HashUnit.PH, TimeUnit.SECOND).value
    under_long_term = current_target_ph < long_term_ph
    over_long_term = current_target_ph > long_term_ph

    # Acceptable bid-count range: between 1 bid per 2 PH/s of current target
    # (lower bound) and 1 bid per 1 PH/s (upper bound), but never above the
    # hard cap. Bounds are integer bid counts. When current target is zero,
    # the range collapses to [0, 0].
    if current_target_ph > 0:
        upper_bid_count = min(
            int(current_target_ph.to_integral_value(rounding="ROUND_CEILING")),
            _HARD_CAP_BIDS,
        )
        lower_bid_count = min(
            int(
                (current_target_ph / Decimal(2)).to_integral_value(
                    rounding="ROUND_CEILING"
                )
            ),
            upper_bid_count,
        )
    else:
        upper_bid_count = lower_bid_count = 0

    best_plan: ReconciliationPlan | None = None
    best_score: Decimal | None = None
    for plan in candidate_plans:
        plan_bid_count = len(plan.unchanged) + len(plan.edits) + len(plan.creates)
        # Hard cap: plans with more than 3 bids are disqualified outright.
        if plan_bid_count > _HARD_CAP_BIDS:
            continue

        plan_score: Decimal = Decimal(0)

        # Compute the plan's effective served hashrate and the price-weighted
        # numerator in a single pass. Bids whose price is below the market
        # target price won't be served — they contribute zero hashrate (and
        # pay nothing), so they're skipped here entirely.
        plan_ph = Decimal(0)  # PH/s of bids that will actually clear the market
        weighted_price_numerator = Decimal(0)  # (sat/PH/Day) * (PH/s)
        for unchanged_bid in plan.unchanged:
            if unchanged_bid.price < target_price:
                continue
            speed = unchanged_bid.speed_limit_ph.to(HashUnit.PH, TimeUnit.SECOND).value
            price = unchanged_bid.price.to(HashUnit.PH, TimeUnit.DAY).sats
            plan_ph += speed
            weighted_price_numerator += Decimal(int(price)) * speed
        for edit_action in plan.edits:
            if edit_action.new_price < target_price:
                continue
            speed = edit_action.new_speed_limit_ph.to(
                HashUnit.PH, TimeUnit.SECOND
            ).value
            price = edit_action.new_price.to(HashUnit.PH, TimeUnit.DAY).sats
            plan_ph += speed
            weighted_price_numerator += Decimal(int(price)) * speed
        for create_action in plan.creates:
            if create_action.config.price < target_price:
                continue
            speed = create_action.config.speed_limit.to(
                HashUnit.PH, TimeUnit.SECOND
            ).value
            price = create_action.config.price.to(HashUnit.PH, TimeUnit.DAY).sats
            plan_ph += speed
            weighted_price_numerator += Decimal(int(price)) * speed

        # Score for getting the right hashrate computed like this:
        # 100_000_000 * (1 - %deviation * wrong_way_multiplier)
        # %deviation is the deviation from the current target hashrate.
        # wrong_way_multiplier is 1 when the plan moves us faster toward the
        # long-term target, 2 otherwise.
        # so, if we are UNDER long term target, and the plan has MORE
        #   hashrate than CURRENT, it's going THE RIGHT WAY (multiplier 1).
        # if we are UNDER long term target, and the plan has LESS hashrate
        #   than CURRENT, it's going the WRONG WAY (multiplier 2).

        if current_target_ph == 0:
            # No relative-deviation reference; fall back to long_term as the
            # divisor so a non-zero plan still carries a deviation penalty.
            denom = long_term_ph if long_term_ph > 0 else Decimal(1)
            deviation_pct = abs(plan_ph - current_target_ph) / denom
        else:
            deviation_pct = abs((plan_ph - current_target_ph) / current_target_ph)

        plan_above_current = plan_ph > current_target_ph
        plan_below_current = plan_ph < current_target_ph
        plan_moves_us_faster_toward_long_term = (
            under_long_term and plan_above_current
        ) or (over_long_term and plan_below_current)
        wrong_way_multiplier = 1 if plan_moves_us_faster_toward_long_term else 2

        plan_score += Decimal(100_000_000) * (
            Decimal(1) - (deviation_pct * wrong_way_multiplier)
        )

        # Bid count score: penalize plans whose bid count falls outside
        # [lower_bid_count, upper_bid_count]. Single-bid plans always
        # carry at least one unit of penalty (fragile to cooldowns).
        # Plans with more than _HARD_CAP_BIDS bids were skipped above.
        bid_count_distance = max(
            lower_bid_count - plan_bid_count,
            plan_bid_count - upper_bid_count,
            0,
        )
        if plan_bid_count == 1:
            bid_count_distance = max(bid_count_distance, 1)
        bid_count_denom = current_target_ph if current_target_ph > 0 else Decimal(1)
        plan_score += Decimal(1_000_000) * (
            Decimal(1) - Decimal(bid_count_distance) / bid_count_denom
        )

        # Cheap is better than expensive.
        # Hashrate-weighted average price across the plan's served bids
        # (sat/PH/Day). Subtracting it makes expensive plans rank lower.
        # Empty plans (or plans with no served bids) contribute nothing.
        if plan_ph > 0:
            weighted_avg_price_sat_per_ph_day = weighted_price_numerator / plan_ph
            plan_score -= weighted_avg_price_sat_per_ph_day

        # Cooldowns are bad
        # Accumulating cooldowns is exponentially bad
        # Having two cooldowns in the same bid is terrible
        # Having two cooldowns of the same feature (price/hashrate) is
        # is worse than having two cooldowns of different feature
        # Some values
        # Penalty is (10_000 per cooldown) / number of bids
        # Plus an extra 10_000 per each bid that activates BOTH cooldowns
        # Plus the abs(number of price cooldowns - the number of hashrate
        #   cooldowns) * 10_000
        price_cooldowns_triggered = 0
        speed_cooldowns_triggered = 0
        bids_triggering_both_cooldowns = 0
        bids_with_new_cooldown = 0
        for edit_action in plan.edits:
            price_decreased = edit_action.new_price < edit_action.bid.price
            speed_decreased = (
                edit_action.new_speed_limit_ph < edit_action.bid.speed_limit_ph
            )
            if price_decreased:
                price_cooldowns_triggered += 1
            if speed_decreased:
                speed_cooldowns_triggered += 1
            if price_decreased and speed_decreased:
                bids_triggering_both_cooldowns += 1
            if price_decreased or speed_decreased:
                bids_with_new_cooldown += 1

        total_cooldowns = price_cooldowns_triggered + speed_cooldowns_triggered
        cooldown_penalty = Decimal(0)
        if plan_bid_count > 0:
            cooldown_penalty += Decimal(10_000) * total_cooldowns / plan_bid_count
            cooldown_penalty += (
                Decimal(10_000) * bids_triggering_both_cooldowns / plan_bid_count
            )
            cooldown_penalty += (
                Decimal(10_000)
                * abs(price_cooldowns_triggered - speed_cooldowns_triggered)
                / plan_bid_count
            )

        # Always keep at least one existing bid cooldown-free. Plans where
        # every existing bid in the plan triggers a new cooldown leave us
        # with no headroom to react next cycle. Flat 5M penalty.
        existing_bids_in_plan = len(plan.edits) + len(plan.unchanged)
        if (
            existing_bids_in_plan > 0
            and bids_with_new_cooldown == existing_bids_in_plan
        ):
            cooldown_penalty += Decimal(5_000_000)

        plan_score -= cooldown_penalty

        # Cancelling an existing bid is expensive (lost ground, lost
        # remaining funds, churn). Flat 10M penalty per cancel discourages
        # the planner from cancelling working bids over marginal score gains.
        plan_score -= Decimal(10_000_000) * len(plan.cancels)

        # Cancel-and-create in the same plan is churn: tearing down a
        # working bid only to spin up a fresh one. Extra 90M penalty when
        # both happen together makes this strictly worse than editing.
        if plan.cancels and plan.creates:
            plan_score -= Decimal(90_000_000)

        if best_score is None or plan_score > best_score:
            best_plan = plan
            best_score = plan_score

    if best_plan is None:
        # Defensive: should be impossible while craft_all_possible_plans
        # always emits the all-cancel + no-create plan (count = 0).
        raise RuntimeError(
            "no candidate plan satisfies the bid hard cap; "
            "expected the all-cancel plan to be present"
        )
    return best_plan


def _plan_reconciliation(
    inputs: TargetHashrateInputs,
    config: TargetHashrateConfig,
) -> ReconciliationPlan:

    all_possible_plans = craft_all_possible_plans(inputs=inputs, config=config)
    best_plan = select_best_plan(
        candidate_plans=all_possible_plans,
        long_term_hashrate_target=inputs.target,
        current_hashrate_target=inputs.needed_hashrate,
        target_price=inputs.target_price,
    )

    return best_plan


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
        2. Plan: enumerate every candidate plan via ``craft_all_possible_plans``
           (cartesian product over per-bid dispositions and a single creation
           slot, with cooldowns gating decreases) and pick the highest-scoring
           one via ``select_best_plan``.
        3. Apply: unless `dry_run`, execute the plan.

    `now` defaults to the current UTC time; tests inject a fixed value.
    """
    if now is None:
        now = datetime.now(UTC)

    inputs = _gather_inputs(client, ocean, address, config, now)
    plan = _plan_reconciliation(inputs, config)
    balance_check = check_balance(
        plan=plan,
        available_sats=inputs.available_balance.available_sat,
    )
    execution_result = _apply_plan(client, plan, dry_run)

    return SetBidsTargetResult(
        inputs=inputs,
        set_bids_result=SetBidsResult(
            plan=plan,
            skipped_bids=inputs.non_manageable_bids,
            balance_check=balance_check,
            execution=execution_result,
        ),
    )
