"""Bitcoin address primitive with structural and checksum validation.

Bech32/bech32m checksum logic adapted from the BIP173/BIP350 reference
implementation (MIT license).
Base58check logic uses stdlib hashlib for the double-SHA256 checksum.
"""

import hashlib
import re

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BASE58_RE = re.compile(f"^[{_BASE58_ALPHABET}]+$")
_BASE58_MAP = {c: i for i, c in enumerate(_BASE58_ALPHABET)}


def _base58_decode(s: str) -> bytes:
    """Decode a base58-encoded string to bytes."""
    n = 0
    for c in s:
        n = n * 58 + _BASE58_MAP[c]
    # Preserve leading zero bytes (encoded as leading '1' chars).
    leading = len(s) - len(s.lstrip("1"))
    raw = n.to_bytes(max((n.bit_length() + 7) // 8, 1), "big")
    return b"\x00" * leading + raw


def _validate_base58check(value: str) -> None:
    """Validate a base58check-encoded address (P2PKH or P2SH)."""
    if not _BASE58_RE.match(value):
        raise ValueError(f"Invalid base58 characters in address: {value!r}")
    if not (25 <= len(value) <= 34):
        raise ValueError(
            f"Base58 address must be 25-34 characters, got {len(value)}: {value!r}"
        )
    decoded = _base58_decode(value)
    if len(decoded) != 25:
        raise ValueError(f"Base58 address decodes to wrong length: {value!r}")
    payload, checksum = decoded[:-4], decoded[-4:]
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if checksum != expected:
        raise ValueError(f"Base58check checksum mismatch: {value!r}")


_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_MAP = {c: i for i, c in enumerate(_BECH32_CHARSET)}
_BECH32_RE = re.compile(r"^bc1[ac-hj-np-z02-9]+$")

_BECH32_CONST = 1
_BECH32M_CONST = 0x2BC830A3


def _bech32_polymod(values: list[int]) -> int:
    """Compute the bech32 checksum polynomial."""
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            chk ^= generator[i] if ((top >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    """Expand the human-readable part for checksum computation."""
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _validate_bech32(value: str) -> None:
    """Validate a bech32/bech32m-encoded address."""
    lower = value.lower()
    if lower != value:
        raise ValueError(f"Bech32 address must be lowercase: {value!r}")
    if not _BECH32_RE.match(lower):
        raise ValueError(f"Invalid bech32 characters in address: {value!r}")
    # bc1q (P2WPKH) = 42, bc1q (P2WSH) = 62, bc1p (taproot) = 62.
    if len(value) not in (42, 62):
        raise ValueError(
            f"Bech32 address must be 42 or 62 characters, got {len(value)}: {value!r}"
        )
    # Decode the data part and verify the checksum.
    data_part = lower[3:]  # after "bc1"
    data = [_BECH32_MAP[c] for c in data_part]
    const = _bech32_polymod(_bech32_hrp_expand("bc") + data)
    # Witness version 0 uses bech32, versions 1+ use bech32m.
    witness_version = data[0]
    if witness_version == 0:
        expected = _BECH32_CONST
    else:
        expected = _BECH32M_CONST
    if const != expected:
        raise ValueError(f"Bech32 checksum mismatch: {value!r}")


def _validate(value: str) -> None:
    """Validate structural properties and checksum of a Bitcoin address.

    Raises:
        ValueError: If the address fails validation.
    """
    if not value:
        raise ValueError("BTC address must not be empty")

    if value.lower().startswith("bc1"):
        _validate_bech32(value)
        return

    if value.startswith(("1", "3")):
        _validate_base58check(value)
        return

    raise ValueError(
        f"Unrecognized address format (expected prefix 1, 3, or bc1): {value!r}"
    )


class BtcAddress:
    """A validated Bitcoin mainnet address.

    Validates prefix, character set, length, and checksum for P2PKH (1...),
    P2SH (3...), bech32 (bc1q...), and bech32m (bc1p...) addresses.
    """

    def __init__(self, value: str) -> None:
        """Create a BTC address, raising ValueError if invalid.

        Args:
            value: The raw address string.

        Raises:
            ValueError: If the address fails validation.
        """
        stripped = value.strip()
        _validate(stripped)
        self._value = stripped

    @property
    def value(self) -> str:
        """The raw address string."""
        return self._value

    def truncated(self) -> str:
        """Shortened form for display: first 7 + '...' + last 4 chars."""
        if len(self._value) <= 14:
            return self._value
        return f"{self._value[:7]}...{self._value[-4:]}"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, BtcAddress):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    def __repr__(self) -> str:
        return f"BtcAddress({self._value!r})"

    def __str__(self) -> str:
        return self._value
