"""Stratum mining protocol URL type."""

from __future__ import annotations

from urllib.parse import urlparse

_VALID_SCHEMES = ("stratum+tcp", "stratum+ssl")


class StratumUrl:
    """A validated stratum mining protocol URL.

    Accepts URLs with schemes ``stratum+tcp`` or ``stratum+ssl``,
    requiring a host and port.
    """

    __slots__ = ("_host", "_port", "_scheme")

    def __init__(self, raw: str) -> None:
        """Parse and validate a stratum URL.

        Args:
            raw: The URL string to parse.

        Raises:
            ValueError: If the URL has an invalid scheme, host, or port.
        """
        parsed = urlparse(raw)

        if parsed.scheme not in _VALID_SCHEMES:
            raise ValueError(
                f"Invalid stratum URL scheme {parsed.scheme!r}, "
                f"expected one of {_VALID_SCHEMES}"
            )

        if not parsed.hostname:
            raise ValueError(f"Stratum URL must have a host: {raw!r}")

        try:
            port = parsed.port
        except ValueError as e:
            raise ValueError(f"Stratum URL has invalid port: {raw!r}") from e

        if port is None:
            raise ValueError(f"Stratum URL must have a port: {raw!r}")

        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = port

    @property
    def scheme(self) -> str:
        """The URL scheme (e.g. 'stratum+tcp')."""
        return self._scheme

    @property
    def host(self) -> str:
        """The hostname."""
        return self._host

    @property
    def port(self) -> int:
        """The port number."""
        return self._port

    def _key(self) -> tuple[str, str, int]:
        return (self.scheme, self.host, self.port)

    def __str__(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def __repr__(self) -> str:
        return f"StratumUrl({str(self)!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, StratumUrl):
            return self._key() == other._key()
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._key())
