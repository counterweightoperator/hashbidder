"""Tests for the reconcile orchestration (balance check + execution)."""

from hashbidder.bid_runner import reconcile
from hashbidder.clients.braiins import AccountBalance
from hashbidder.domain.balance_check import BalanceStatus
from hashbidder.domain.sats import Sats
from tests.conftest import (
    UPSTREAM,
    FakeClient,
    make_bid_config,
    make_config,
)


def _balance(available: int) -> AccountBalance:
    return AccountBalance(
        available_sat=Sats(available),
        blocked_sat=Sats(0),
        total_sat=Sats(available),
    )


class TestReconcileBalanceCheck:
    """Tests covering the balance-check gate in reconcile."""

    def test_dry_run_runs_balance_check(self) -> None:
        """A dry run still surfaces a balance check result."""
        client = FakeClient(
            current_bids=(),
            account_balance=_balance(10_000_000_000),
        )
        config = make_config(make_bid_config(500, "5.0"), upstream=UPSTREAM)

        result = reconcile(client, config, dry_run=True)

        assert result.execution is None
        assert result.balance_check.status == BalanceStatus.SUFFICIENT

    def test_insufficient_balance_aborts_execution(self) -> None:
        """INSUFFICIENT balance short-circuits: no API mutations."""
        client = FakeClient(
            current_bids=(),
            # default_amount in make_config is 100_000; give 50_000 to be short.
            account_balance=_balance(50_000),
        )
        config = make_config(make_bid_config(500, "5.0"), upstream=UPSTREAM)

        result = reconcile(client, config, dry_run=False)

        assert result.execution is None
        assert result.balance_check.status == BalanceStatus.INSUFFICIENT
        # No mutations were issued against the API.
        mutations = [
            c for c in client.calls if c[0] in ("create_bid", "edit_bid", "cancel_bid")
        ]
        assert mutations == []

    def test_low_balance_still_executes(self) -> None:
        """LOW balance is a warning, not a block — execution proceeds."""
        # Burn rate for 500 sat/PH/Day @ 5 PH/s is 9M sat/hour.
        # 71 hours * 9M = 639M available → runway under 72h (LOW),
        # but easily covers the 100_000 sat required.
        client = FakeClient(
            current_bids=(),
            account_balance=_balance(9_000_000 * 71),
        )
        config = make_config(make_bid_config(500, "5.0"), upstream=UPSTREAM)

        result = reconcile(client, config, dry_run=False)

        assert result.balance_check.status == BalanceStatus.LOW
        assert result.execution is not None
        # One create was issued.
        assert any(c[0] == "create_bid" for c in client.calls)

    def test_sufficient_balance_executes_normally(self) -> None:
        """A comfortable balance executes the plan as before."""
        client = FakeClient(
            current_bids=(),
            account_balance=_balance(10_000_000_000),
        )
        config = make_config(make_bid_config(500, "5.0"), upstream=UPSTREAM)

        result = reconcile(client, config, dry_run=False)

        assert result.balance_check.status == BalanceStatus.SUFFICIENT
        assert result.execution is not None
        assert any(c[0] == "create_bid" for c in client.calls)
