"""Tests for the execution engine: retries, failures, atomic pairs."""

from hashbidder.bid_runner import (
    POST_EXECUTE_REFETCH_DELAY_SECONDS,
    ActionStatus,
    execute_plan,
)
from hashbidder.clients.braiins import (
    ApiError,
    ClOrderId,
    CreateBidResult,
    Upstream,
)
from hashbidder.domain.bid_planning import plan_bid_changes
from hashbidder.domain.hashrate import Hashrate, HashratePrice
from hashbidder.domain.sats import Sats
from tests.conftest import (
    OTHER_UPSTREAM,
    UPSTREAM,
    FakeClient,
    make_bid_config,
    make_config,
    make_user_bid,
)


def _no_sleep(_seconds: float) -> None:
    """No-op sleep for tests."""


class TestRetries:
    """Tests for retry logic on transient errors."""

    def test_transient_error_retries_then_succeeds(self) -> None:
        """A 429 on first attempt retries and succeeds on second."""
        bid = make_user_bid("B1", 500, "5.0")
        errors = {("cancel_bid", "B1"): [ApiError(429, "rate limited")]}
        client = FakeClient(current_bids=(bid,), errors=errors)
        config = make_config(upstream=UPSTREAM)
        plan = plan_bid_changes(config, client.get_current_bids())

        result = execute_plan(client, plan, sleep=_no_sleep)

        # Two outcomes: failed attempt, then success.
        cancel_outcomes = [
            o for o in result.outcomes if o.status != ActionStatus.SKIPPED
        ]
        assert len(cancel_outcomes) == 2
        assert cancel_outcomes[0].status == ActionStatus.FAILED
        assert cancel_outcomes[0].attempt == 1
        assert cancel_outcomes[1].status == ActionStatus.SUCCEEDED
        assert client.get_current_bids() == ()

    def test_permanent_error_no_retry(self) -> None:
        """A 400 error fails immediately without retry."""
        bid = make_user_bid("B1", 500, "5.0")
        errors = {("cancel_bid", "B1"): [ApiError(400, "bad request")]}
        client = FakeClient(current_bids=(bid,), errors=errors)
        config = make_config(upstream=UPSTREAM)
        plan = plan_bid_changes(config, client.get_current_bids())

        result = execute_plan(client, plan, sleep=_no_sleep)

        cancel_outcomes = [
            o for o in result.outcomes if o.status == ActionStatus.FAILED
        ]
        assert len(cancel_outcomes) == 1
        assert cancel_outcomes[0].attempt is None  # No attempt tracking for permanent.
        assert cancel_outcomes[0].error == "bad request"

    def test_all_retries_exhausted(self) -> None:
        """Three consecutive 500 errors exhaust retries."""
        bid = make_user_bid("B1", 500, "5.0")
        errors = {
            ("cancel_bid", "B1"): [
                ApiError(500, "internal error"),
                ApiError(500, "internal error"),
                ApiError(500, "internal error"),
            ]
        }
        client = FakeClient(current_bids=(bid,), errors=errors)
        config = make_config(upstream=UPSTREAM)
        plan = plan_bid_changes(config, client.get_current_bids())

        result = execute_plan(client, plan, sleep=_no_sleep)

        failed = [o for o in result.outcomes if o.status == ActionStatus.FAILED]
        assert len(failed) == 3
        # First two are intermediate retries, last is final failure.
        assert failed[0].attempt == 1
        assert failed[1].attempt == 2
        assert failed[2].attempt == 3


class TestContinueOnFailure:
    """Tests that execution continues after individual action failures."""

    def test_failure_does_not_block_subsequent_actions(self) -> None:
        """A failed edit does not prevent a subsequent create."""
        bid = make_user_bid("B1", 400, "5.0")
        client = FakeClient(
            current_bids=(bid,),
            errors={("edit_bid", "B1"): [ApiError(400, "cooldown")]},
        )
        config = make_config(make_bid_config(500, "5.0"), make_bid_config(300, "10.0"))
        plan = plan_bid_changes(config, client.get_current_bids())

        result = execute_plan(client, plan, sleep=_no_sleep)

        statuses = [o.status for o in result.outcomes]
        assert ActionStatus.FAILED in statuses
        assert ActionStatus.SUCCEEDED in statuses
        # The create should have succeeded.
        assert len(result.final_bids) == 2  # original bid + new created bid


class TestAtomicUpstreamPairs:
    """Tests for atomic cancel+create upstream mismatch pairs."""

    def test_cancel_fails_skips_linked_create(self) -> None:
        """If upstream-mismatch cancel fails, linked create is skipped."""
        bid = make_user_bid("B1", 500, "5.0", upstream=OTHER_UPSTREAM)
        errors = {("cancel_bid", "B1"): [ApiError(400, "grace period")]}
        client = FakeClient(current_bids=(bid,), errors=errors)
        config = make_config(make_bid_config(500, "5.0"), upstream=UPSTREAM)
        plan = plan_bid_changes(config, client.get_current_bids())

        result = execute_plan(client, plan, sleep=_no_sleep)

        statuses = [o.status for o in result.outcomes]
        assert ActionStatus.FAILED in statuses
        assert ActionStatus.SKIPPED in statuses
        # Bid still exists — cancel failed, create was skipped.
        assert len(result.final_bids) == 1
        assert result.final_bids[0].id == bid.id

    def test_cancel_succeeds_create_fails_no_rollback(self) -> None:
        """If cancel succeeds but create fails, no rollback — just report."""
        bid = make_user_bid("B1", 500, "5.0", upstream=OTHER_UPSTREAM)
        client = FakeClient(current_bids=(bid,), errors={})
        config = make_config(make_bid_config(500, "5.0"), upstream=UPSTREAM)
        plan = plan_bid_changes(config, client.get_current_bids())
        # Inject error on the create's cl_order_id — we don't know it ahead
        # of time, so inject on any create_bid call via a custom approach.
        # Instead, we'll use the fact that FakeClient errors key on cl_order_id
        # which we can't predict. Let's subclass FakeClient for this test.

        class FailingCreateClient(FakeClient):
            """FakeClient that fails on the first create_bid call."""

            def __init__(self, **kwargs: object) -> None:
                """Initialize with a flag to fail once."""
                super().__init__(**kwargs)  # type: ignore[arg-type]
                self._create_fail = True

            def create_bid(
                self,
                upstream: Upstream,
                amount_sat: Sats,
                price: HashratePrice,
                speed_limit: Hashrate,
                cl_order_id: ClOrderId,
            ) -> CreateBidResult:
                """Fail the first create, succeed after."""
                if self._create_fail:
                    self._create_fail = False
                    raise ApiError(400, "insufficient balance")
                return super().create_bid(
                    upstream, amount_sat, price, speed_limit, cl_order_id
                )

        failing_client = FailingCreateClient(current_bids=(bid,))
        result = execute_plan(failing_client, plan, sleep=_no_sleep)

        statuses = [o.status for o in result.outcomes]
        assert statuses.count(ActionStatus.SUCCEEDED) == 1  # cancel
        assert statuses.count(ActionStatus.FAILED) == 1  # create
        # Bid was canceled, create failed — no bids left.
        assert len(result.final_bids) == 0

    def test_unmatched_cancel_failure_does_not_skip_creates(self) -> None:
        """A failed UNMATCHED cancel does not skip pure creates."""
        # 2 existing bids, 1 config entry → B1 gets matched (edit to 300/10),
        # B2 is unmatched (cancel). Cancel of B2 fails, but the pure create
        # from the extra config entry should still succeed.
        bids = (
            make_user_bid("B1", 500, "5.0", amount=200_000, upstream=UPSTREAM),
            make_user_bid("B2", 600, "3.0", remaining=50_000, upstream=UPSTREAM),
        )
        errors = {("cancel_bid", "B2"): [ApiError(400, "grace period")]}
        client = FakeClient(current_bids=bids, errors=errors)
        config = make_config(make_bid_config(300, "10.0"), upstream=UPSTREAM)
        plan = plan_bid_changes(config, client.get_current_bids())

        result = execute_plan(client, plan, sleep=_no_sleep)

        statuses = [o.status for o in result.outcomes]
        assert ActionStatus.FAILED in statuses
        assert ActionStatus.SKIPPED not in statuses
        # The edit of B1 should have succeeded.
        assert ActionStatus.SUCCEEDED in statuses


class TestExecutionOrder:
    """Tests that actions execute in the correct order."""

    def test_cancels_before_edits_before_creates(self) -> None:
        """Actions execute in order: cancels, edits, creates."""
        bids = (
            make_user_bid("B1", 400, "5.0", amount=200_000, upstream=UPSTREAM),
            make_user_bid("B2", 600, "3.0", remaining=50_000, upstream=UPSTREAM),
        )
        client = FakeClient(current_bids=bids)
        config = make_config(make_bid_config(500, "5.0"), make_bid_config(300, "10.0"))
        plan = plan_bid_changes(config, client.get_current_bids())

        result = execute_plan(client, plan, sleep=_no_sleep)

        # Extract the action types in order from outcomes.
        action_types = [type(o.action).__name__ for o in result.outcomes]
        cancel_idx = [i for i, t in enumerate(action_types) if t == "CancelAction"]
        edit_idx = [i for i, t in enumerate(action_types) if t == "EditAction"]
        create_idx = [i for i, t in enumerate(action_types) if t == "CreateAction"]

        if cancel_idx and edit_idx:
            assert max(cancel_idx) < min(edit_idx)
        if edit_idx and create_idx:
            assert max(edit_idx) < min(create_idx)
        if cancel_idx and create_idx:
            assert max(cancel_idx) < min(create_idx)


class TestResultsSummary:
    """Tests for the results summary counting."""

    def test_mixed_results_counted_correctly(self) -> None:
        """Succeeded, failed, and skipped are counted correctly."""
        bid_match = make_user_bid("B1", 500, "5.0", upstream=OTHER_UPSTREAM)
        bid_extra = make_user_bid("B2", 600, "3.0", upstream=UPSTREAM)
        errors = {("cancel_bid", "B1"): [ApiError(400, "nope")]}
        client = FakeClient(current_bids=(bid_match, bid_extra), errors=errors)
        config = make_config(make_bid_config(500, "5.0"), upstream=UPSTREAM)
        plan = plan_bid_changes(config, client.get_current_bids())

        result = execute_plan(client, plan, sleep=_no_sleep)

        succeeded = sum(
            1 for o in result.outcomes if o.status == ActionStatus.SUCCEEDED
        )
        failed = sum(1 for o in result.outcomes if o.status == ActionStatus.FAILED)
        skipped = sum(1 for o in result.outcomes if o.status == ActionStatus.SKIPPED)
        assert succeeded >= 1
        assert failed >= 1
        assert skipped >= 1


class TestRefetchDelay:
    """Braiins caches bid state briefly after mutations; we sleep before refetch."""

    def test_sleeps_before_refetch_when_plan_has_actions(self) -> None:
        """Non-empty plan triggers a pre-refetch sleep."""
        bid = make_user_bid("B1", 500, "5.0", upstream=OTHER_UPSTREAM)
        client = FakeClient(current_bids=(bid,))
        config = make_config(upstream=UPSTREAM)
        plan = plan_bid_changes(config, client.get_current_bids())
        assert plan.cancels  # sanity: plan is non-empty

        sleeps: list[float] = []
        execute_plan(client, plan, sleep=sleeps.append)

        assert POST_EXECUTE_REFETCH_DELAY_SECONDS in sleeps

    def test_no_sleep_when_plan_is_empty(self) -> None:
        """Empty plan skips the pre-refetch sleep."""
        client = FakeClient(current_bids=())
        config = make_config(upstream=UPSTREAM)
        plan = plan_bid_changes(config, client.get_current_bids())
        assert not plan.cancels
        assert not plan.edits
        assert not plan.creates

        sleeps: list[float] = []
        execute_plan(client, plan, sleep=sleeps.append)

        assert sleeps == []
