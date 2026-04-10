"""Tests for MempoolClient HTTP serialization and error handling."""

from decimal import Decimal

import httpx
import pytest

from hashbidder.domain.block_height import BlockHeight
from hashbidder.domain.sats import Sats
from hashbidder.mempool_client import MempoolClient, MempoolError

BASE_URL = httpx.URL("http://test-mempool")


def _make_client(handler: httpx.MockTransport) -> MempoolClient:
    return MempoolClient(
        base_url=BASE_URL,
        http_client=httpx.Client(transport=handler),
    )


class TestGetTip:
    """Tests for MempoolClient.get_tip."""

    def test_parses_height_and_difficulty(self) -> None:
        """Fetches tip height then block details for difficulty."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            if "/api/blocks/tip/height" in str(request.url):
                return httpx.Response(200, text="840000")
            # /api/v1/blocks/840000
            return httpx.Response(
                200,
                json=[{"difficulty": 83148355189239.77, "id": "abc"}],
            )

        client = _make_client(httpx.MockTransport(handler))
        tip = client.get_tip()

        assert tip.height == BlockHeight(840_000)
        assert tip.difficulty == Decimal("83148355189239.77")
        assert len(captured) == 2
        assert "/api/blocks/tip/height" in str(captured[0].url)
        assert "/api/v1/blocks/840000" in str(captured[1].url)

    def test_height_error_raises(self) -> None:
        """Non-2xx on tip height raises MempoolError."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="service unavailable")

        client = _make_client(httpx.MockTransport(handler))

        with pytest.raises(MempoolError) as exc_info:
            client.get_tip()
        assert exc_info.value.status_code == 503
        assert "service unavailable" in exc_info.value.message


class TestGetRewardStats:
    """Tests for MempoolClient.get_reward_stats."""

    def test_parses_total_fee(self) -> None:
        """Sends correct URL and parses totalFee from response."""
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                json={
                    "totalReward": "1000000000000",
                    "totalFee": "50000000000",
                    "totalTx": 500000,
                },
            )

        client = _make_client(httpx.MockTransport(handler))
        stats = client.get_reward_stats(2016)

        assert stats.total_fee == Sats(50_000_000_000)
        assert "/api/v1/mining/reward-stats/2016" in str(captured[0].url)

    def test_error_raises(self) -> None:
        """Non-2xx on reward stats raises MempoolError."""

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal error")

        client = _make_client(httpx.MockTransport(handler))

        with pytest.raises(MempoolError) as exc_info:
            client.get_reward_stats(2016)
        assert exc_info.value.status_code == 500
