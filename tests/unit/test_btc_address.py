"""Tests for BtcAddress validation."""

import pytest

from hashbidder.domain.btc_address import BtcAddress


class TestValidAddresses:
    """Known-good addresses that must be accepted."""

    def test_p2pkh(self) -> None:
        """Satoshi's genesis coinbase address (P2PKH, prefix 1)."""
        addr = BtcAddress("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        assert addr.value == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"

    def test_p2sh(self) -> None:
        """P2SH address (prefix 3)."""
        addr = BtcAddress("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy")
        assert addr.value == "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"

    def test_bech32_p2wpkh(self) -> None:
        """Native segwit P2WPKH (bc1q, 42 chars)."""
        addr = BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
        assert addr.value == "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"

    def test_bech32_p2wsh(self) -> None:
        """Native segwit P2WSH (bc1q, 62 chars), from a real block."""
        addr = BtcAddress(
            "bc1qwzrryqr3ja8w7hnja2spmkgfdcgvqwp5swz4af4ngsjecfz0w0pqud7k38"
        )
        assert len(addr.value) == 62

    def test_bech32m_taproot(self) -> None:
        """Taproot P2TR (bc1p, 62 chars), from a real block."""
        addr = BtcAddress(
            "bc1pgy84xdguk0e6jzaazrn3kfxvtf6mnerxvdq9uyrwejqan48l6u3qdhkz53"
        )
        assert addr.value.startswith("bc1p")

    def test_whitespace_stripped(self) -> None:
        """Leading/trailing whitespace is stripped before validation."""
        addr = BtcAddress("  1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa  ")
        assert addr.value == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


class TestInvalidAddresses:
    """Addresses that must be rejected."""

    def test_empty(self) -> None:
        """Empty string is rejected."""
        with pytest.raises(ValueError, match="empty"):
            BtcAddress("")

    def test_whitespace_only(self) -> None:
        """Whitespace-only string is rejected."""
        with pytest.raises(ValueError, match="empty"):
            BtcAddress("   ")

    def test_unrecognized_prefix(self) -> None:
        """WIF private key format (prefix 5) is not an address."""
        with pytest.raises(ValueError, match="Unrecognized"):
            BtcAddress("5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ")

    def test_bech32_uppercase_rejected(self) -> None:
        """Bech32 must be all lowercase."""
        with pytest.raises(ValueError, match="lowercase"):
            BtcAddress("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4")

    def test_bech32_wrong_length(self) -> None:
        """Bech32 address with valid charset but wrong length."""
        with pytest.raises(ValueError, match="42 or 62"):
            BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7k")

    def test_bech32_invalid_character(self) -> None:
        """Bech32 charset does not include 'b', 'i', 'o', '1'."""
        with pytest.raises(ValueError, match="Invalid bech32"):
            BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3b4")

    def test_base58_invalid_character(self) -> None:
        """Base58 charset does not include '0', 'O', 'I', 'l'."""
        with pytest.raises(ValueError, match="Invalid base58"):
            BtcAddress("1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf0a")

    def test_base58_too_short(self) -> None:
        """Base58 address shorter than 25 chars is rejected."""
        with pytest.raises(ValueError, match="25-34"):
            BtcAddress("1A1zP1")


class TestChecksumTampering:
    """Valid addresses with one character changed to break the checksum."""

    def test_p2pkh_last_char_changed(self) -> None:
        """Change last char of a valid P2PKH address."""
        with pytest.raises(ValueError, match="checksum"):
            BtcAddress("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb")

    def test_p2pkh_middle_char_changed(self) -> None:
        """Change a middle char of a valid P2PKH address."""
        # Original: 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa
        # Changed 'G' -> 'H' at position 10
        with pytest.raises(ValueError, match="checksum"):
            BtcAddress("1A1zP1eP5QHefi2DMPTfTL5SLmv7DivfNa")

    def test_p2sh_checksum_tampered(self) -> None:
        """Change last char of a valid P2SH address."""
        with pytest.raises(ValueError, match="checksum"):
            BtcAddress("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLz")

    def test_bech32_last_char_changed(self) -> None:
        """Change last char of a valid bech32 address."""
        # Original: bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4
        # Changed '4' -> '5'
        with pytest.raises(ValueError, match="checksum"):
            BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5")

    def test_bech32_middle_char_changed(self) -> None:
        """Change a middle char of a valid bech32 address."""
        # Original: bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4
        # Changed 'e' -> 'f' at position 10
        with pytest.raises(ValueError, match="checksum"):
            BtcAddress("bc1qw508d6qfjxtdg4y5r3zarvary0c5xw7kv8f3t4")

    def test_bech32m_taproot_tampered(self) -> None:
        """Change a char in a valid taproot address."""
        # Original: bc1pgy84xdguk0e6jzaazrn3kfxvtf6mnerxvdq9uyrwejqan48l6u3qdhkz53
        # Changed last '3' -> '4'
        with pytest.raises(ValueError, match="checksum"):
            BtcAddress("bc1pgy84xdguk0e6jzaazrn3kfxvtf6mnerxvdq9uyrwejqan48l6u3qdhkz54")


class TestTruncated:
    """Tests for the truncated display format."""

    def test_long_address_truncated(self) -> None:
        """Bech32 address shows first 7 + ... + last 4."""
        addr = BtcAddress("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")
        assert addr.truncated() == "bc1qw50...f3t4"

    def test_str_returns_full_address(self) -> None:
        """str() returns the full untruncated address."""
        addr = BtcAddress("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        assert str(addr) == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"

    def test_equality(self) -> None:
        """Same address string produces equal BtcAddress objects."""
        a = BtcAddress("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        b = BtcAddress("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        assert a == b
        assert hash(a) == hash(b)

    def test_inequality(self) -> None:
        """Different addresses are not equal."""
        a = BtcAddress("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
        b = BtcAddress("3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy")
        assert a != b
