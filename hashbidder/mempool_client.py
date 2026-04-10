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

DEFAULT_MEMPOOL_URL = httpx.URL("https://mempool.bitcoinbarcelona.xyz")


class MempoolError(Exception):
    """An error returned by the mempool.space API."""

    def __init__(self, status_code: int, message: str) -> None:
        """Initialize with the HTTP status code and error message."""
        self.status_code = status_code
        self.message = message
        super().__init__(f"HTTP {status_code}: {message}")


@dataclass(frozen=True)
class ChainStats:
    """Chain tip info and reward statistics over a block range."""

    tip_height: BlockHeight
    difficulty: Decimal
    total_fee: Sats


class MempoolSource(Protocol):
    """Protocol for mempool data sources."""

    def get_chain_stats(self, block_count: int) -> ChainStats:
        """Fetch chain tip and reward stats for the last block_count blocks."""
        ...


class MempoolClient:
    """HTTP client for the mempool.space API."""

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

    def get_chain_stats(self, block_count: int) -> ChainStats:
        """Fetch chain tip and reward stats atomically.

        Raises:
            MempoolError: If the API returns a non-2xx response.
        """
        # Why we extract the tip height from reward-stats instead of
        # calling /api/blocks/tip/height:
        #
        # We need three values that must be consistent with each other:
        # tip height, total fees over the last N blocks, and difficulty
        # at the tip. The mempool.space API has no single endpoint that
        # returns all three, so we need at least two calls.
        #
        # If we fetched the tip height separately, a new block could be
        # mined between the two requests: tip would be N+1, but fees
        # would still cover blocks ending at N — a silent inconsistency
        # that skews the hashvalue calculation.
        #
        # The reward-stats endpoint conveniently includes an `endBlock`
        # field: the height of the last block in the fee window. By
        # using that as our tip, the height and fees are guaranteed to
        # refer to the same range. The second call (fetching difficulty
        # for that specific block) is safe because difficulty is an
        # immutable property of a mined block — it can't change after
        # the fact.
        stats_url = f"{self._base_url}{self._REWARD_STATS_PATH}/{block_count}"
        logger.debug("GET %s", stats_url)
        resp = self._http.get(stats_url)
        if not resp.is_success:
            self._raise_error(resp)
        data: dict[str, object] = resp.json()
        tip_height = BlockHeight(int(str(data["endBlock"])))
        total_fee = Sats(int(str(data["totalFee"])))

        # Get difficulty for the tip block.
        block_url = f"{self._base_url}{self._BLOCKS_PATH}/{tip_height.value}"
        logger.debug("GET %s", block_url)
        resp = self._http.get(block_url)
        if not resp.is_success:
            self._raise_error(resp)
        blocks: list[dict[str, object]] = json.loads(resp.text, parse_float=Decimal)

        return ChainStats(
            tip_height=tip_height,
            difficulty=Decimal(str(blocks[0]["difficulty"])),
            total_fee=total_fee,
        )
