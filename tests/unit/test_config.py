"""Tests for bid config parsing."""

import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies
from hypothesis.strategies import DrawFn, composite

from hashbidder.cli.config import load_config
from hashbidder.domain.bid_config import SetBidsConfig, TargetHashrateConfig
from hashbidder.domain.hashrate import Hashrate, HashUnit
from hashbidder.domain.sats import Sats
from hashbidder.domain.time_unit import TimeUnit

_positive_int = strategies.integers(min_value=1, max_value=10**9)

_positive_decimal = strategies.decimals(
    min_value=Decimal("1"),
    max_value=Decimal("1000000"),
    allow_nan=False,
    allow_infinity=False,
    places=3,
)

_port = strategies.integers(min_value=1, max_value=65535)
_hostname = strategies.from_regex(r"[a-z]{1,10}\.[a-z]{2,4}", fullmatch=True)
_identity = strategies.from_regex(r"[a-zA-Z0-9_]{1,20}", fullmatch=True)


@composite
def _bid_toml_entry(draw: DrawFn) -> tuple[str, int, Decimal]:
    """Draw a single [[bids]] TOML block and its expected values."""
    price = draw(_positive_int)
    speed = draw(_positive_decimal)
    block = f"""\
[[bids]]
price_sat_per_ph_day = {price}
speed_limit_ph_s = {speed}
"""
    return block, price, speed


@composite
def _valid_config_toml(
    draw: DrawFn,
) -> tuple[str, int, str, str, list[tuple[int, Decimal]]]:
    """Draw a complete valid TOML config and expected parsed values."""
    amount = draw(_positive_int)
    host = draw(_hostname)
    port = draw(_port)
    url = f"stratum+tcp://{host}:{port}"
    identity = draw(_identity)
    n_bids = draw(strategies.integers(min_value=0, max_value=5))
    bid_entries = [draw(_bid_toml_entry()) for _ in range(n_bids)]

    bid_blocks = "".join(block for block, _, _ in bid_entries)
    expected_bids = [(price, speed) for _, price, speed in bid_entries]

    toml = f"""\
default_amount_sat = {amount}

[upstream]
url = "{url}"
identity = "{identity}"

{bid_blocks}"""
    return toml, amount, url, identity, expected_bids


def _write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(content)
    return p


class TestLoadConfig:
    """Tests for load_config."""

    def test_valid_config(self, tmp_path: Path) -> None:
        """A well-formed config parses correctly into domain types."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"

[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0

[[bids]]
price_sat_per_ph_day = 300
speed_limit_ph_s = 10.0
""",
        )
        config = load_config(path)
        assert isinstance(config, SetBidsConfig)

        assert config.default_amount == Sats(100000)
        assert str(config.upstream.url) == "stratum+tcp://pool.example.com:3333"
        assert config.upstream.identity == "worker1"
        assert len(config.bids) == 2

        assert config.bids[0].price.sats == Sats(500)
        assert config.bids[0].price.per == Hashrate(
            Decimal(1), HashUnit.PH, TimeUnit.DAY
        )
        assert config.bids[0].speed_limit == Hashrate(
            Decimal("5.0"), HashUnit.PH, TimeUnit.SECOND
        )

        assert config.bids[1].price.sats == Sats(300)
        assert config.bids[1].speed_limit == Hashrate(
            Decimal("10.0"), HashUnit.PH, TimeUnit.SECOND
        )

    def test_empty_bids_list(self, tmp_path: Path) -> None:
        """Config with no bids section is valid (empty bids list)."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 50000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"
""",
        )
        config = load_config(path)
        assert isinstance(config, SetBidsConfig)

        assert config.bids == ()

    def test_missing_default_amount_sat(self, tmp_path: Path) -> None:
        """Missing default_amount_sat raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"
""",
        )
        with pytest.raises(ValueError, match="default_amount_sat"):
            load_config(path)

    def test_missing_upstream(self, tmp_path: Path) -> None:
        """Missing upstream section raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 100000
""",
        )
        with pytest.raises(ValueError, match="upstream"):
            load_config(path)

    def test_missing_upstream_url(self, tmp_path: Path) -> None:
        """Missing upstream url raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 100000

[upstream]
identity = "worker1"
""",
        )
        with pytest.raises(ValueError, match="url"):
            load_config(path)

    def test_missing_upstream_identity(self, tmp_path: Path) -> None:
        """Missing upstream identity raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
""",
        )
        with pytest.raises(ValueError, match="identity"):
            load_config(path)

    def test_missing_bid_price(self, tmp_path: Path) -> None:
        """Missing price_sat_per_ph_day in a bid raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"

[[bids]]
speed_limit_ph_s = 5.0
""",
        )
        with pytest.raises(ValueError, match="price_sat_per_ph_day"):
            load_config(path)

    def test_missing_bid_speed_limit(self, tmp_path: Path) -> None:
        """Missing speed_limit_ph_s in a bid raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"

[[bids]]
price_sat_per_ph_day = 500
""",
        )
        with pytest.raises(ValueError, match="speed_limit_ph_s"):
            load_config(path)

    def test_invalid_toml(self, tmp_path: Path) -> None:
        """Invalid TOML syntax raises ValueError."""
        path = _write_toml(tmp_path, "this is not valid toml [[[")
        with pytest.raises(ValueError, match="Invalid TOML"):
            load_config(path)

    def test_bad_type_default_amount(self, tmp_path: Path) -> None:
        """Non-integer default_amount_sat raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = "not a number"

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"
""",
        )
        with pytest.raises(ValueError, match="default_amount_sat must be an integer"):
            load_config(path)

    def test_bad_type_bid_price(self, tmp_path: Path) -> None:
        """Non-integer bid price raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"

[[bids]]
price_sat_per_ph_day = "expensive"
speed_limit_ph_s = 5.0
""",
        )
        with pytest.raises(ValueError, match="price_sat_per_ph_day must be an integer"):
            load_config(path)

    def test_zero_speed_limit_rejected(self, tmp_path: Path) -> None:
        """Zero speed_limit_ph_s raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"

[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 0
""",
        )
        with pytest.raises(ValueError, match="speed_limit_ph_s must be positive"):
            load_config(path)

    def test_negative_speed_limit_rejected(self, tmp_path: Path) -> None:
        """Negative speed_limit_ph_s raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"

[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = -1.0
""",
        )
        with pytest.raises(ValueError, match="speed_limit_ph_s must be positive"):
            load_config(path)

    def test_duplicate_bids_allowed(self, tmp_path: Path) -> None:
        """Duplicate bid entries in config are allowed."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"

[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0

[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0
""",
        )
        config = load_config(path)
        assert isinstance(config, SetBidsConfig)
        assert len(config.bids) == 2
        assert config.bids[0] == config.bids[1]

    def test_invalid_upstream_url(self, tmp_path: Path) -> None:
        """Non-stratum upstream URL raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
default_amount_sat = 100000

[upstream]
url = "http://pool.example.com:3333"
identity = "worker1"
""",
        )
        with pytest.raises(ValueError, match="Invalid upstream URL"):
            load_config(path)

    def test_file_not_found(self, tmp_path: Path) -> None:
        """Missing config file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.toml")

    def test_explicit_bids_mode_field(self, tmp_path: Path) -> None:
        """Mode = 'explicit-bids' parses the same as absent mode."""
        path = _write_toml(
            tmp_path,
            """\
mode = "explicit-bids"
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"

[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0
""",
        )
        config = load_config(path)
        assert isinstance(config, SetBidsConfig)
        assert len(config.bids) == 1

    def test_target_hashrate_mode(self, tmp_path: Path) -> None:
        """A valid target-hashrate config parses into TargetHashrateConfig."""
        path = _write_toml(
            tmp_path,
            """\
mode = "target-hashrate"
default_amount_sat = 100000
target_hashrate_ph_s = 10.0

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"
""",
        )
        config = load_config(path)
        assert isinstance(config, TargetHashrateConfig)
        assert config.default_amount == Sats(100000)
        assert config.target_hashrate == Hashrate(
            Decimal("10.0"), HashUnit.PH, TimeUnit.SECOND
        )
        assert config.upstream.identity == "worker1"

    def test_target_hashrate_rejects_bids_section(self, tmp_path: Path) -> None:
        """target-hashrate mode with [[bids]] sections raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
mode = "target-hashrate"
default_amount_sat = 100000
target_hashrate_ph_s = 10.0

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"

[[bids]]
price_sat_per_ph_day = 500
speed_limit_ph_s = 5.0
""",
        )
        with pytest.raises(ValueError, match="target-hashrate"):
            load_config(path)

    def test_target_hashrate_missing_target(self, tmp_path: Path) -> None:
        """Missing target_hashrate_ph_s raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
mode = "target-hashrate"
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"
""",
        )
        with pytest.raises(ValueError, match="target_hashrate_ph_s"):
            load_config(path)

    def test_target_hashrate_non_positive_target(self, tmp_path: Path) -> None:
        """target_hashrate_ph_s <= 0 raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
mode = "target-hashrate"
default_amount_sat = 100000
target_hashrate_ph_s = 0

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"
""",
        )
        with pytest.raises(ValueError, match="target_hashrate_ph_s must be positive"):
            load_config(path)

    def test_invalid_mode(self, tmp_path: Path) -> None:
        """An unknown mode value raises ValueError."""
        path = _write_toml(
            tmp_path,
            """\
mode = "nonsense"
default_amount_sat = 100000

[upstream]
url = "stratum+tcp://pool.example.com:3333"
identity = "worker1"
""",
        )
        with pytest.raises(ValueError, match="Invalid mode"):
            load_config(path)


def _load_from_string(toml_str: str) -> SetBidsConfig:
    """Write TOML to a temp file and load it as an explicit-bids config."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(toml_str)
        f.flush()
        config = load_config(Path(f.name))
    assert isinstance(config, SetBidsConfig)
    return config


class TestLoadConfigProperties:
    """Property-based tests for load_config."""

    @given(data=_valid_config_toml())
    @settings(max_examples=50)
    def test_valid_config_always_parses(
        self, data: tuple[str, int, str, str, list[tuple[int, Decimal]]]
    ) -> None:
        """Any well-formed TOML with valid fields parses without error."""
        toml_str, amount, url, identity, expected_bids = data

        config = _load_from_string(toml_str)

        assert config.default_amount == Sats(amount)
        assert str(config.upstream.url) == url
        assert config.upstream.identity == identity
        assert len(config.bids) == len(expected_bids)

    @given(data=_valid_config_toml())
    @settings(max_examples=50)
    def test_prices_are_sat_per_ph_day(
        self, data: tuple[str, int, str, str, list[tuple[int, Decimal]]]
    ) -> None:
        """Parsed prices are always denominated in sat/PH/Day."""
        toml_str, _, _, _, expected_bids = data

        config = _load_from_string(toml_str)

        for bid, (expected_price, _) in zip(config.bids, expected_bids, strict=True):
            assert bid.price.sats == Sats(expected_price)
            assert bid.price.per.hash_unit == HashUnit.PH
            assert bid.price.per.time_unit == TimeUnit.DAY

    @given(data=_valid_config_toml())
    @settings(max_examples=50)
    def test_speed_limits_are_ph_per_second(
        self, data: tuple[str, int, str, str, list[tuple[int, Decimal]]]
    ) -> None:
        """Parsed speed limits are always in PH/s."""
        toml_str, _, _, _, expected_bids = data

        config = _load_from_string(toml_str)

        for bid, (_, expected_speed) in zip(config.bids, expected_bids, strict=True):
            assert bid.speed_limit.value == expected_speed
            assert bid.speed_limit.hash_unit == HashUnit.PH
            assert bid.speed_limit.time_unit == TimeUnit.SECOND

    @given(data=_valid_config_toml())
    @settings(max_examples=50)
    def test_config_types_are_frozen(
        self, data: tuple[str, int, str, str, list[tuple[int, Decimal]]]
    ) -> None:
        """Parsed config and its bids are immutable."""
        toml_str, _, _, _, _ = data

        config = _load_from_string(toml_str)

        with pytest.raises(AttributeError):
            config.default_amount = Sats(0)  # type: ignore[misc]
        for bid in config.bids:
            with pytest.raises(AttributeError):
                bid.price = bid.price  # type: ignore[misc]
