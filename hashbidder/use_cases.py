"""Hashbidder use cases."""

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from hashbidder.client import (
    ApiError,
    BidId,
    ClOrderId,
    HashpowerClient,
    OrderBook,
    UserBid,
)
from hashbidder.config import SetBidsConfig
from hashbidder.domain.bitcoin import BLOCKS_PER_EPOCH
from hashbidder.domain.sats import Sats
from hashbidder.hashvalue import HashvalueComponents, compute_hashvalue
from hashbidder.mempool_client import MempoolSource
from hashbidder.reconcile import (
    MANAGEABLE_STATUSES,
    CancelAction,
    CancelReason,
    CreateAction,
    EditAction,
    ReconciliationPlan,
    reconcile,
)

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5.0


def ping(client: HashpowerClient) -> OrderBook:
    """Fetch the current order book.

    Args:
        client: The hashpower market client to use.

    Returns:
        The current spot order book snapshot.
    """
    return client.get_orderbook()


def get_current_bids(client: HashpowerClient) -> tuple[UserBid, ...]:
    """Fetch the authenticated user's active bids.

    Args:
        client: The hashpower market client to use.

    Returns:
        The user's currently active spot bids.
    """
    return client.get_current_bids()


def get_hashvalue(mempool: MempoolSource) -> HashvalueComponents:
    """Compute the current hashvalue from on-chain data.

    Args:
        mempool: The mempool data source to use.

    Returns:
        All intermediate components and the final hashvalue.
    """
    tip = mempool.get_tip()
    stats = mempool.get_reward_stats(BLOCKS_PER_EPOCH)
    return compute_hashvalue(
        difficulty=tip.difficulty,
        tip_height=tip.height,
        total_fees=Sats(stats.total_fee),
    )


@dataclass(frozen=True)
class SetBidsResult:
    """Result of the set-bids reconciliation."""

    plan: ReconciliationPlan
    skipped_bids: tuple[UserBid, ...]


def set_bids(client: HashpowerClient, config: SetBidsConfig) -> SetBidsResult:
    """Reconcile current bids against the desired config.

    Args:
        client: The hashpower market client to use.
        config: The desired bid configuration.

    Returns:
        The reconciliation plan and any skipped (non-manageable) bids.
    """
    current_bids = client.get_current_bids()
    plan = reconcile(config, current_bids)
    skipped = tuple(b for b in current_bids if b.status not in MANAGEABLE_STATUSES)
    return SetBidsResult(plan=plan, skipped_bids=skipped)


class ActionStatus(Enum):
    """Outcome status of a single execution action."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ActionOutcome:
    """Result of executing a single action or retry attempt.

    For retry attempts that will be followed by another try,
    status is FAILED with attempt/max_attempts set.
    The final outcome has status SUCCEEDED, FAILED, or SKIPPED.
    """

    action: CancelAction | EditAction | CreateAction
    status: ActionStatus
    error: str | None = None
    created_id: BidId | None = None
    attempt: int | None = None
    max_attempts: int | None = None


@dataclass(frozen=True)
class ExecutionResult:
    """Result of executing a reconciliation plan."""

    outcomes: tuple[ActionOutcome, ...]
    final_bids: tuple[UserBid, ...]


def _call_cancel(client: HashpowerClient, cancel: CancelAction) -> BidId | None:
    """Call the cancel API. Returns None (cancel has no created ID)."""
    client.cancel_bid(cancel.bid.id)
    return None


def _call_edit(client: HashpowerClient, edit: EditAction) -> BidId | None:
    """Call the edit API. Returns None (edit has no created ID)."""
    client.edit_bid(edit.bid.id, edit.new_price, edit.new_speed_limit_ph)
    return None


def _call_create(client: HashpowerClient, create: CreateAction) -> BidId | None:
    """Call the create API. Returns the new bid ID."""
    cl_order_id = ClOrderId(str(uuid.uuid4()))
    result = client.create_bid(
        upstream=create.upstream,
        amount_sat=create.amount,
        price=create.config.price,
        speed_limit=create.config.speed_limit,
        cl_order_id=cl_order_id,
    )
    return result.id


def _dispatch(
    client: HashpowerClient, action: CancelAction | EditAction | CreateAction
) -> BidId | None:
    """Dispatch an action to the appropriate client method."""
    if isinstance(action, CancelAction):
        return _call_cancel(client, action)
    if isinstance(action, EditAction):
        return _call_edit(client, action)
    return _call_create(client, action)


def _execute_with_retries(
    client: HashpowerClient,
    action: CancelAction | EditAction | CreateAction,
    outcomes: list[ActionOutcome],
    sleep: Callable[[float], None],
) -> bool:
    """Execute an action with retries. Returns True if succeeded.

    Appends all outcomes (including intermediate retry failures) to the list.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            created_id = _dispatch(client, action)
            outcomes.append(
                ActionOutcome(
                    action=action,
                    status=ActionStatus.SUCCEEDED,
                    created_id=created_id,
                )
            )
            return True
        except ApiError as e:
            is_last = attempt == MAX_ATTEMPTS
            if not e.is_transient or is_last:
                outcomes.append(
                    ActionOutcome(
                        action=action,
                        status=ActionStatus.FAILED,
                        error=e.message,
                        attempt=attempt if e.is_transient else None,
                        max_attempts=MAX_ATTEMPTS if e.is_transient else None,
                    )
                )
                return False
            # Transient error, will retry.
            outcomes.append(
                ActionOutcome(
                    action=action,
                    status=ActionStatus.FAILED,
                    error=e.message,
                    attempt=attempt,
                    max_attempts=MAX_ATTEMPTS,
                )
            )
            sleep(RETRY_DELAY_SECONDS)
    return False  # pragma: no cover


def execute_plan(
    client: HashpowerClient,
    plan: ReconciliationPlan,
    sleep: Callable[[float], None] = time.sleep,
) -> ExecutionResult:
    """Execute a reconciliation plan against the API.

    Executes in order: cancels, edits, creates.
    Each action retries up to 3 times on transient errors (429, 5xx).
    If an upstream-mismatch cancel fails, its linked create is skipped.

    Args:
        client: The hashpower market client to use.
        plan: The reconciliation plan to execute.
        sleep: Sleep function for retry delays (injectable for testing).

    Returns:
        Outcomes for each action and the final bid state from the API.
    """
    outcomes: list[ActionOutcome] = []
    failed_cancel_ids: set[BidId] = set()

    for cancel in plan.cancels:
        succeeded = _execute_with_retries(client, cancel, outcomes, sleep)
        if not succeeded and cancel.reason == CancelReason.UPSTREAM_MISMATCH:
            failed_cancel_ids.add(cancel.bid.id)

    for edit in plan.edits:
        _execute_with_retries(client, edit, outcomes, sleep)

    for create in plan.creates:
        if create.replaces is not None and create.replaces.id in failed_cancel_ids:
            outcomes.append(ActionOutcome(action=create, status=ActionStatus.SKIPPED))
            continue
        _execute_with_retries(client, create, outcomes, sleep)

    final_bids = client.get_current_bids()
    return ExecutionResult(outcomes=tuple(outcomes), final_bids=final_bids)
