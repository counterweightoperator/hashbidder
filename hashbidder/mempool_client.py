"""Mempool.space API client."""

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

import httpx

from hashbidder.domain.block_height import BlockHeight
from hashbidder.domain.sats import Sats

logger = logging.getLogger(__name__)


class MempoolError(Exception):
    """An error returned by the mempool.space API."""

    def __init__(self, status_code: int, message: str) -> None:
        """Initialize with the HTTP status code and error message."""
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code}: {message}")


@dataclass(frozen=True)
class BlockTipInfo:
    """Current chain tip info."""

    height: BlockHeight
    difficulty: Decimal


@dataclass(frozen=True)
class RewardStats:
    """Mining reward statistics over a range of blocks."""

    total_fee: Sats


class MempoolSource(Protocol):
    """Protocol for mempool data sources."""

    def get_tip(self) -> BlockTipInfo:
        """Fetch the current chain tip height and difficulty."""
        ...

    def get_reward_stats(self, block_count: int) -> RewardStats:
        """Fetch reward statistics over the last block_count blocks."""
        ...


class MempoolClient:
    """HTTP client for the mempool.space API."""

    _TIP_HEIGHT_PATH = "/api/blocks/tip/height"
    _BLOCKS_PATH = "/api/v1/blocks"
    _REWARD_STATS_PATH = "/api/v1/mining/reward-stats"

    def __init__(self, base_url: httpx.URL, http_client: httpx.Client) -> None:
        """Initialize the client.

        Args:
            base_url: The base URL of the mempool.space instance.
            http_client: The httpx.Client to use for requests.
        """
        self._base_url = base_url
        self._http = http_client

    def _raise_error(self, response: httpx.Response) -> None:
        """Raise a MempoolError from a non-2xx response."""
        raise MempoolError(
            response.status_code,
            response.text or response.reason_phrase or "Unknown error",
        )

    def get_tip(self) -> BlockTipInfo:
        """Fetch the current chain tip height and difficulty.

        Raises:
            MempoolError: If the API returns a non-2xx response.
        """
        # Get tip height.
        height_url = f"{self._base_url}{self._TIP_HEIGHT_PATH}"
        logger.debug("GET %s", height_url)
        resp = self._http.get(height_url)
        if not resp.is_success:
            self._raise_error(resp)
        height = BlockHeight(int(resp.text))

        # Get block at that height for difficulty.
        block_url = f"{self._base_url}{self._BLOCKS_PATH}/{height.value}"
        logger.debug("GET %s", block_url)
        resp = self._http.get(block_url)
        if not resp.is_success:
            self._raise_error(resp)
        blocks: list[dict[str, object]] = json.loads(resp.text, parse_float=Decimal)
        return BlockTipInfo(
            height=height, difficulty=Decimal(str(blocks[0]["difficulty"]))
        )

    def get_reward_stats(self, block_count: int) -> RewardStats:
        """Fetch reward statistics over the last block_count blocks.

        Raises:
            MempoolError: If the API returns a non-2xx response.
        """
        url = f"{self._base_url}{self._REWARD_STATS_PATH}/{block_count}"
        logger.debug("GET %s", url)
        resp = self._http.get(url)
        if not resp.is_success:
            self._raise_error(resp)
        data: dict[str, object] = resp.json()
        return RewardStats(total_fee=Sats(int(str(data["totalFee"]))))
