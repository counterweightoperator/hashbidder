"""Tests for StratumUrl domain type."""

import pytest
from hypothesis import given, settings, strategies
from hypothesis.strategies import DrawFn, composite

from hashbidder.domain.stratum_url import StratumUrl

_scheme = strategies.sampled_from(["stratum+tcp", "stratum+ssl"])
_host = strategies.from_regex(r"[a-z]{1,10}\.[a-z]{2,4}", fullmatch=True)
_port = strategies.integers(min_value=1, max_value=65535)


@composite
def _valid_stratum_url(draw: DrawFn) -> str:
    scheme = draw(_scheme)
    host = draw(_host)
    port = draw(_port)
    return f"{scheme}://{host}:{port}"


class TestStratumUrl:
    """Tests for StratumUrl."""

    def test_valid_tcp(self) -> None:
        """stratum+tcp URL parses correctly."""
        url = StratumUrl("stratum+tcp://pool.example.com:3333")
        assert url.scheme == "stratum+tcp"
        assert url.host == "pool.example.com"
        assert url.port == 3333
        assert str(url) == "stratum+tcp://pool.example.com:3333"

    def test_valid_ssl(self) -> None:
        """stratum+ssl URL parses correctly."""
        url = StratumUrl("stratum+ssl://secure.pool.io:8443")
        assert url.scheme == "stratum+ssl"
        assert url.host == "secure.pool.io"
        assert url.port == 8443

    def test_invalid_scheme_http(self) -> None:
        """HTTP scheme is rejected."""
        with pytest.raises(ValueError, match="Invalid stratum URL scheme"):
            StratumUrl("http://pool.example.com:3333")

    def test_invalid_scheme_bare(self) -> None:
        """Plain TCP scheme is rejected."""
        with pytest.raises(ValueError, match="Invalid stratum URL scheme"):
            StratumUrl("tcp://pool.example.com:3333")

    def test_missing_host(self) -> None:
        """URL without host is rejected."""
        with pytest.raises(ValueError, match="must have a host"):
            StratumUrl("stratum+tcp://:3333")

    def test_missing_port(self) -> None:
        """URL without port is rejected."""
        with pytest.raises(ValueError, match="must have a port"):
            StratumUrl("stratum+tcp://pool.example.com")

    def test_equality(self) -> None:
        """Two StratumUrls with the same value are equal."""
        a = StratumUrl("stratum+tcp://pool.example.com:3333")
        b = StratumUrl("stratum+tcp://pool.example.com:3333")
        assert a == b
        assert hash(a) == hash(b)

    def test_equality_trailing_slash(self) -> None:
        """Trailing slash does not affect equality."""
        a = StratumUrl("stratum+tcp://167.172.107.33:23334")
        b = StratumUrl("stratum+tcp://167.172.107.33:23334/")
        assert a == b
        assert hash(a) == hash(b)

    def test_str_is_normalized(self) -> None:
        """str() always produces the canonical form without trailing slash."""
        url = StratumUrl("stratum+tcp://167.172.107.33:23334/")
        assert str(url) == "stratum+tcp://167.172.107.33:23334"

    def test_inequality(self) -> None:
        """Different StratumUrls are not equal."""
        a = StratumUrl("stratum+tcp://pool.example.com:3333")
        b = StratumUrl("stratum+tcp://pool.example.com:4444")
        assert a != b

    @given(raw=_valid_stratum_url())
    @settings(max_examples=50)
    def test_valid_urls_always_parse(self, raw: str) -> None:
        """Any well-formed stratum URL parses without error."""
        url = StratumUrl(raw)
        assert url.scheme in ("stratum+tcp", "stratum+ssl")
        assert url.host
        assert url.port > 0

    @given(raw=_valid_stratum_url())
    @settings(max_examples=50)
    def test_str_roundtrip(self, raw: str) -> None:
        """str(StratumUrl(x)) preserves the original URL."""
        assert str(StratumUrl(raw)) == raw
