"""Tests for the stateful FakeClient test double."""

import pytest

from hashbidder.client import ApiError, BidId, BidStatus, ClOrderId
from hashbidder.domain.sats import Sats
from tests.conftest import UPSTREAM, FakeClient, make_bid_config, make_user_bid


class TestFakeClientCancel:
    """Tests for FakeClient.cancel_bid."""

    def test_cancel_removes_bid(self) -> None:
        """Canceling a bid removes it from the internal state."""
        bid = make_user_bid("B1", 500, "5.0")
        client = FakeClient(current_bids=(bid,))

        client.cancel_bid(BidId("B1"))

        assert client.get_current_bids() == ()

    def test_cancel_nonexistent_raises(self) -> None:
        """Canceling a nonexistent bid raises ApiError 404."""
        client = FakeClient()
        with pytest.raises(ApiError, match="not found"):
            client.cancel_bid(BidId("B999"))


class TestFakeClientEdit:
    """Tests for FakeClient.edit_bid."""

    def test_edit_updates_fields(self) -> None:
        """Editing a bid updates price and speed limit, preserves other fields."""
        bid = make_user_bid("B1", 500, "5.0")
        client = FakeClient(current_bids=(bid,))
        cfg = make_bid_config(600, "10.0")

        client.edit_bid(BidId("B1"), cfg.price, cfg.speed_limit)

        updated = client.get_current_bids()[0]
        assert updated.price == cfg.price
        assert updated.speed_limit_ph == cfg.speed_limit
        assert updated.amount_sat == bid.amount_sat

    def test_edit_nonexistent_raises(self) -> None:
        """Editing a nonexistent bid raises ApiError 404."""
        client = FakeClient()
        cfg = make_bid_config(600, "10.0")
        with pytest.raises(ApiError, match="not found"):
            client.edit_bid(BidId("B999"), cfg.price, cfg.speed_limit)


class TestFakeClientCreate:
    """Tests for FakeClient.create_bid."""

    def test_create_adds_bid(self) -> None:
        """Creating a bid adds it to internal state with CREATED status."""
        client = FakeClient()
        cfg = make_bid_config(500, "5.0")

        result = client.create_bid(
            UPSTREAM, Sats(100_000), cfg.price, cfg.speed_limit, ClOrderId("cl-1")
        )

        assert result.id.startswith("B")
        bids = client.get_current_bids()
        assert len(bids) == 1
        assert bids[0].id == result.id
        assert bids[0].status == BidStatus.CREATED

    def test_create_ids_are_unique(self) -> None:
        """Each created bid gets a unique ID."""
        client = FakeClient()
        cfg = make_bid_config(500, "5.0")

        r1 = client.create_bid(
            UPSTREAM, Sats(100_000), cfg.price, cfg.speed_limit, ClOrderId("a")
        )
        r2 = client.create_bid(
            UPSTREAM, Sats(100_000), cfg.price, cfg.speed_limit, ClOrderId("b")
        )

        assert r1.id != r2.id


class TestFakeClientErrorInjection:
    """Tests for FakeClient error injection."""

    def test_error_raised_then_succeeds(self) -> None:
        """Injected error is raised once, then the real logic runs."""
        bid = make_user_bid("B1", 500, "5.0")
        errors = {("cancel_bid", "B1"): [ApiError(429, "rate limited")]}
        client = FakeClient(current_bids=(bid,), errors=errors)

        with pytest.raises(ApiError, match="rate limited"):
            client.cancel_bid(BidId("B1"))

        # Second call succeeds — error list exhausted.
        client.cancel_bid(BidId("B1"))
        assert client.get_current_bids() == ()

    def test_calls_are_recorded(self) -> None:
        """All method calls are recorded in order."""
        bid = make_user_bid("B1", 500, "5.0")
        client = FakeClient(current_bids=(bid,))
        cfg = make_bid_config(600, "10.0")

        client.edit_bid(BidId("B1"), cfg.price, cfg.speed_limit)
        client.cancel_bid(BidId("B1"))

        assert client.calls == [("edit_bid", "B1"), ("cancel_bid", "B1")]
