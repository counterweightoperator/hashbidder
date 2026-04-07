"""Bid configuration file parsing."""

import tomllib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from hashbidder.client import Upstream
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.stratum_url import StratumUrl
from hashbidder.domain.time_unit import TimeUnit


@dataclass(frozen=True)
class BidConfig:
    """A single desired bid from the config file."""

    price: HashratePrice
    speed_limit: Hashrate


@dataclass(frozen=True)
class SetBidsConfig:
    """Parsed set-bids configuration."""

    default_amount: Sats
    upstream: Upstream
    bids: tuple[BidConfig, ...]


def load_config(path: Path) -> SetBidsConfig:
    """Load and validate a set-bids TOML config file.

    Args:
        path: Path to the TOML config file.

    Returns:
        Parsed and validated configuration.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        ValueError: If the config is invalid.
    """
    with path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"Invalid TOML: {e}") from e

    if "default_amount_sat" not in data:
        raise ValueError("Missing required field: default_amount_sat")
    default_amount_sat = data["default_amount_sat"]
    if not isinstance(default_amount_sat, int):
        raise ValueError("default_amount_sat must be an integer")

    if "upstream" not in data:
        raise ValueError("Missing required section: [upstream]")
    upstream_data = data["upstream"]
    for field in ("url", "identity"):
        if field not in upstream_data:
            raise ValueError(f"Missing required upstream field: {field}")
    try:
        url = StratumUrl(upstream_data["url"])
    except ValueError as e:
        raise ValueError(f"Invalid upstream URL: {e}") from e
    upstream = Upstream(url=url, identity=upstream_data["identity"])

    bids_data = data.get("bids", [])
    bids = []
    for i, bid_data in enumerate(bids_data):
        if "price_sat_per_ph_day" not in bid_data:
            raise ValueError(f"Bid {i}: missing required field: price_sat_per_ph_day")
        if "speed_limit_ph_s" not in bid_data:
            raise ValueError(f"Bid {i}: missing required field: speed_limit_ph_s")

        price_raw = bid_data["price_sat_per_ph_day"]
        if not isinstance(price_raw, int):
            raise ValueError(f"Bid {i}: price_sat_per_ph_day must be an integer")

        try:
            speed_raw = Decimal(str(bid_data["speed_limit_ph_s"]))
        except InvalidOperation as e:
            raise ValueError(f"Bid {i}: speed_limit_ph_s must be a number") from e

        if speed_raw <= 0:
            raise ValueError(f"Bid {i}: speed_limit_ph_s must be positive")

        bids.append(
            BidConfig(
                price=HashratePrice(
                    sats=Sats(price_raw),
                    per=Hashrate(Decimal(1), HashUnit.PH, TimeUnit.DAY),
                ),
                speed_limit=Hashrate(speed_raw, HashUnit.PH, TimeUnit.SECOND),
            )
        )

    return SetBidsConfig(
        default_amount=Sats(default_amount_sat),
        upstream=upstream,
        bids=tuple(bids),
    )
