"""Tests for BraiinsClient HTTP serialization and error handling."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

import httpx
import pytest

from hashbidder.client import (
    ApiError,
    BidId,
    BraiinsClient,
    ClOrderId,
    Upstream,
)
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.stratum_url import StratumUrl
from hashbidder.domain.time_unit import TimeUnit

API_KEY = "test-api-key"
BASE_URL = httpx.URL("http://test-api")


def _make_client(handler: httpx.MockTransport) -> BraiinsClient:
    return BraiinsClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        http_client=httpx.Client(transport=handler),
    )


UPSTREAM = Upstream(
    url=StratumUrl("stratum+tcp://pool.example.com:3333"),
    identity="worker1",
)


class TestCreateBid:
    """Tests for BraiinsClient.create_bid serialization."""

    def test_request_body_and_response(self) -> None:
        """Create sends correct body and parses the response ID."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"id": "B999"})

        client = _make_client(httpx.MockTransport(handler))

        # 500 sat/PH/day → 500_000 sat/EH/day
        price = HashratePrice(
            sats=Sats(500), per=Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.DAY)
        )
        speed = Hashrate(Decimal("5.0"), HashUnit.PH, TimeUnit.SECOND)

        result = client.create_bid(
            upstream=UPSTREAM,
            amount_sat=Sats(100_000),
            price=price,
            speed_limit=speed,
            cl_order_id=ClOrderId("order-123"),
        )

        assert result.id == BidId("B999")

        req = captured[0]
        assert req.method == "POST"
        body = json.loads(req.content)
        assert body["amount_sat"] == 100_000
        assert body["price_sat"] == 500_000  # PH/day → EH/day
        assert body["speed_limit_ph"] == 5.0
        assert body["cl_order_id"] == "order-123"
        assert body["dest_upstream"]["url"] == "stratum+tcp://pool.example.com:3333"
        assert body["dest_upstream"]["identity"] == "worker1"
        assert req.headers["apikey"] == API_KEY


class TestEditBid:
    """Tests for BraiinsClient.edit_bid serialization."""

    def test_request_body(self) -> None:
        """Edit sends bid_id, price in EH/day, and speed as OptionalDouble."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={})

        client = _make_client(httpx.MockTransport(handler))

        price = HashratePrice(
            sats=Sats(300), per=Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.DAY)
        )
        speed = Hashrate(Decimal("10.0"), HashUnit.PH, TimeUnit.SECOND)

        client.edit_bid(BidId("B123"), new_price=price, new_speed_limit=speed)

        req = captured[0]
        assert req.method == "PUT"
        body = json.loads(req.content)
        assert body["bid_id"] == "B123"
        assert body["new_price_sat"] == 300_000  # PH/day → EH/day
        assert body["new_speed_limit_ph"] == {"value": 10.0}
        assert req.headers["apikey"] == API_KEY


class TestCancelBid:
    """Tests for BraiinsClient.cancel_bid serialization."""

    def test_request_body(self) -> None:
        """Cancel sends order_id in JSON body via DELETE."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"affected_ids": ["B456"]})

        client = _make_client(httpx.MockTransport(handler))
        client.cancel_bid(BidId("B456"))

        req = captured[0]
        assert req.method == "DELETE"
        body = json.loads(req.content)
        assert body["order_id"] == "B456"
        assert req.headers["apikey"] == API_KEY


class TestGetCurrentBids:
    """Tests for BraiinsClient.get_current_bids parsing."""

    @staticmethod
    def _bid_response_body() -> dict[str, object]:
        return {
            "items": [
                {
                    "bid": {
                        "id": "B42",
                        "price_sat": 500_000,
                        "speed_limit_ph": "5.0",
                        "amount_sat": 100_000,
                        "status": "BID_STATUS_ACTIVE",
                        "last_updated": "2026-04-12T10:30:00+00:00",
                        "dest_upstream": {
                            "url": "stratum+tcp://pool.example.com:3333",
                            "identity": "worker1",
                        },
                    },
                    "state_estimate": {
                        "progress_pct": "10",
                        "amount_remaining_sat": 90_000,
                    },
                },
            ]
        }

    def test_parses_last_updated(self) -> None:
        """last_updated is parsed from the bid response JSON as a datetime."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=self._bid_response_body())

        client = _make_client(httpx.MockTransport(handler))
        bids = client.get_current_bids()

        assert len(bids) == 1
        assert bids[0].id == BidId("B42")
        assert bids[0].last_updated == datetime(2026, 4, 12, 10, 30, tzinfo=UTC)


class TestGetMarketSettings:
    """Tests for BraiinsClient.get_market_settings parsing."""

    def test_parses_cooldown_periods(self) -> None:
        """Both cooldown fields are parsed from /spot/settings response."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                json={
                    "min_bid_price_decrease_period_s": 600,
                    "min_bid_speed_limit_decrease_period_s": 300,
                },
            )

        client = _make_client(httpx.MockTransport(handler))
        settings = client.get_market_settings()

        assert settings.min_bid_price_decrease_period == timedelta(seconds=600)
        assert settings.min_bid_speed_limit_decrease_period == timedelta(seconds=300)
        assert captured[0].method == "GET"
        assert captured[0].url.path.endswith("/spot/settings")


class TestApiErrorParsing:
    """Tests for error response handling."""

    def test_grpc_message_header_decoded(self) -> None:
        """A grpc-message header is URL-decoded into the ApiError message."""

        def handler(_request: httpx.Request) -> httpx.Response:
            encoded = quote("grace period not elapsed")
            return httpx.Response(400, headers={"grpc-message": encoded}, text="")

        client = _make_client(httpx.MockTransport(handler))

        with pytest.raises(ApiError) as exc_info:
            client.cancel_bid(BidId("B1"))
        assert exc_info.value.status_code == 400
        assert exc_info.value.message == "grace period not elapsed"
        assert not exc_info.value.is_transient

    def test_json_body_message_extracted(self) -> None:
        """A JSON body with a 'message' field is extracted into ApiError."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={
                    "message": "You cannot consume this service",
                    "request_id": "abc123",
                },
            )

        client = _make_client(httpx.MockTransport(handler))

        with pytest.raises(ApiError) as exc_info:
            client.cancel_bid(BidId("B1"))
        assert exc_info.value.status_code == 403
        assert exc_info.value.message == "You cannot consume this service"

    def test_fallback_to_response_text(self) -> None:
        """Without grpc-message or JSON message, falls back to response body text."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="bad request body")

        client = _make_client(httpx.MockTransport(handler))

        with pytest.raises(ApiError) as exc_info:
            client.cancel_bid(BidId("B1"))
        assert exc_info.value.message == "bad request body"

    def test_429_is_transient(self) -> None:
        """A 429 response is classified as transient."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="rate limited")

        client = _make_client(httpx.MockTransport(handler))

        with pytest.raises(ApiError) as exc_info:
            client.create_bid(
                upstream=UPSTREAM,
                amount_sat=Sats(100),
                price=HashratePrice(
                    sats=Sats(100),
                    per=Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.DAY),
                ),
                speed_limit=Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.SECOND),
                cl_order_id=ClOrderId("x"),
            )
        assert exc_info.value.status_code == 429
        assert exc_info.value.is_transient

    def test_500_is_transient(self) -> None:
        """A 500 response is classified as transient."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal error")

        client = _make_client(httpx.MockTransport(handler))

        with pytest.raises(ApiError) as exc_info:
            client.edit_bid(
                BidId("B1"),
                new_price=HashratePrice(
                    sats=Sats(100),
                    per=Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.DAY),
                ),
                new_speed_limit=Hashrate(Decimal("1"), HashUnit.PH, TimeUnit.SECOND),
            )
        assert exc_info.value.status_code == 500
        assert exc_info.value.is_transient
