"""Stratum mining protocol URL type."""

from __future__ import annotations

import httpx

_VALID_SCHEMES = ("stratum+tcp", "stratum+ssl")


class StratumUrl:
    """A validated stratum mining protocol URL.

    Accepts URLs with schemes ``stratum+tcp`` or ``stratum+ssl``,
    requiring a host and port.
    """

    __slots__ = ("_url",)

    def __init__(self, raw: str) -> None:
        """Parse and validate a stratum URL.

        Args:
            raw: The URL string to parse.

        Raises:
            ValueError: If the URL has an invalid scheme, host, or port.
        """
        url = httpx.URL(raw)

        scheme = url.scheme
        if scheme not in _VALID_SCHEMES:
            raise ValueError(
                f"Invalid stratum URL scheme {scheme!r}, "
                f"expected one of {_VALID_SCHEMES}"
            )

        if not url.host:
            raise ValueError(f"Stratum URL must have a host: {raw!r}")

        if url.port is None:
            raise ValueError(f"Stratum URL must have a port: {raw!r}")

        self._url = url

    @property
    def scheme(self) -> str:
        """The URL scheme (e.g. 'stratum+tcp')."""
        return self._url.scheme

    @property
    def host(self) -> str:
        """The hostname."""
        return self._url.host

    @property
    def port(self) -> int:
        """The port number."""
        assert self._url.port is not None
        return self._url.port

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
