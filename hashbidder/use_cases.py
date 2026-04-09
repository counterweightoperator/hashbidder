"""Hashbidder use cases."""

import uuid
from dataclasses import dataclass
from enum import Enum

from hashbidder.client import BidId, ClOrderId, HashpowerClient, OrderBook, UserBid
from hashbidder.config import SetBidsConfig
from hashbidder.reconcile import (
    MANAGEABLE_STATUSES,
    CancelAction,
    CreateAction,
    EditAction,
    ReconciliationPlan,
    reconcile,
)


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
    """Result of executing a single action."""

    action: CancelAction | EditAction | CreateAction
    status: ActionStatus
    error: str | None = None
    created_id: BidId | None = None


@dataclass(frozen=True)
class ExecutionResult:
    """Result of executing a reconciliation plan."""

    outcomes: tuple[ActionOutcome, ...]
    final_bids: tuple[UserBid, ...]


def _execute_cancel(client: HashpowerClient, cancel: CancelAction) -> ActionOutcome:
    """Execute a single cancel action."""
    client.cancel_bid(cancel.bid.id)
    return ActionOutcome(action=cancel, status=ActionStatus.SUCCEEDED)


def _execute_edit(client: HashpowerClient, edit: EditAction) -> ActionOutcome:
    """Execute a single edit action."""
    client.edit_bid(edit.bid.id, edit.new_price, edit.new_speed_limit_ph)
    return ActionOutcome(action=edit, status=ActionStatus.SUCCEEDED)


def _execute_create(client: HashpowerClient, create: CreateAction) -> ActionOutcome:
    """Execute a single create action."""
    cl_order_id = ClOrderId(str(uuid.uuid4()))
    result = client.create_bid(
        upstream=create.upstream,
        amount_sat=create.amount,
        price=create.config.price,
        speed_limit=create.config.speed_limit,
        cl_order_id=cl_order_id,
    )
    return ActionOutcome(
        action=create, status=ActionStatus.SUCCEEDED, created_id=result.id
    )


def execute_plan(client: HashpowerClient, plan: ReconciliationPlan) -> ExecutionResult:
    """Execute a reconciliation plan against the API.

    Executes in order: cancels, edits, creates.

    Args:
        client: The hashpower market client to use.
        plan: The reconciliation plan to execute.

    Returns:
        Outcomes for each action and the final bid state from the API.
    """
    outcomes: list[ActionOutcome] = []

    for cancel in plan.cancels:
        outcomes.append(_execute_cancel(client, cancel))

    for edit in plan.edits:
        outcomes.append(_execute_edit(client, edit))

    for create in plan.creates:
        outcomes.append(_execute_create(client, create))

    final_bids = client.get_current_bids()
    return ExecutionResult(outcomes=tuple(outcomes), final_bids=final_bids)
