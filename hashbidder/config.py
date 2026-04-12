"""Bid configuration file parsing."""

import tomllib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

from hashbidder.client import Upstream
from hashbidder.domain.hashrate import Hashrate, HashratePrice, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.stratum_url import StratumUrl
from hashbidder.domain.time_unit import TimeUnit


class ConfigMode(Enum):
    """Which set-bids config format a file uses."""

    EXPLICIT_BIDS = "explicit-bids"
    TARGET_HASHRATE = "target-hashrate"


@dataclass(frozen=True)
class BidConfig:
    """A single desired bid from the config file."""

    price: HashratePrice
    speed_limit: Hashrate


@dataclass(frozen=True)
class SetBidsConfig:
    """Parsed set-bids configuration (explicit bids mode)."""

    default_amount: Sats
    upstream: Upstream
    bids: tuple[BidConfig, ...]


@dataclass(frozen=True)
class TargetHashrateConfig:
    """Parsed set-bids configuration for target-hashrate mode."""

    default_amount: Sats
    upstream: Upstream
    target_hashrate: Hashrate
    max_bids: int


def load_config(path: Path) -> SetBidsConfig | TargetHashrateConfig:
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

    mode_raw = data.get("mode")
    if mode_raw is None:
        mode = ConfigMode.EXPLICIT_BIDS
    else:
        try:
            mode = ConfigMode(mode_raw)
        except ValueError as e:
            valid = ", ".join(repr(m.value) for m in ConfigMode)
            raise ValueError(
                f"Invalid mode {mode_raw!r}: must be one of {valid}"
            ) from e

    default_amount, upstream = _parse_common(data)

    if mode is ConfigMode.TARGET_HASHRATE:
        return _parse_target_hashrate(data, default_amount, upstream)

    return _parse_explicit_bids(data, default_amount, upstream)


def _parse_common(data: dict[str, Any]) -> tuple[Sats, Upstream]:
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
    return Sats(default_amount_sat), upstream


def _parse_explicit_bids(
    data: dict[str, Any], default_amount: Sats, upstream: Upstream
) -> SetBidsConfig:
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
        default_amount=default_amount,
        upstream=upstream,
        bids=tuple(bids),
    )


def _parse_target_hashrate(
    data: dict[str, Any], default_amount: Sats, upstream: Upstream
) -> TargetHashrateConfig:
    if "bids" in data:
        raise ValueError("target-hashrate mode does not accept [[bids]] sections")

    if "target_hashrate_ph_s" not in data:
        raise ValueError("Missing required field: target_hashrate_ph_s")
    try:
        target_raw = Decimal(str(data["target_hashrate_ph_s"]))
    except InvalidOperation as e:
        raise ValueError("target_hashrate_ph_s must be a number") from e
    if target_raw <= 0:
        raise ValueError("target_hashrate_ph_s must be positive")

    if "max_bids" not in data:
        raise ValueError("Missing required field: max_bids")
    max_bids = data["max_bids"]
    if not isinstance(max_bids, int) or isinstance(max_bids, bool):
        raise ValueError("max_bids must be an integer")
    if max_bids < 1:
        raise ValueError("max_bids must be >= 1")

    return TargetHashrateConfig(
        default_amount=default_amount,
        upstream=upstream,
        target_hashrate=Hashrate(target_raw, HashUnit.PH, TimeUnit.SECOND),
        max_bids=max_bids,
    )
