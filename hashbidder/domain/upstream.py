"""Upstream pool specification for a bid."""

from dataclasses import dataclass

from hashbidder.domain.stratum_url import StratumUrl


@dataclass(frozen=True)
class Upstream:
    """Upstream pool specification for a bid."""

    url: StratumUrl
    identity: str
