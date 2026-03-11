from dataclasses import dataclass
from typing import Optional

from eth_account import Account
from web3 import Web3


@dataclass
class WalletKeys:
    private_key: str | None = None
    address: str | None = None


class KeyStore:
    """In-memory key store with validation and checksum normalization."""

    def __init__(self) -> None:
        self._keys = WalletKeys()

    def set_private_key(self, private_key: str) -> str:
        normalized = self._normalize_private_key(private_key)
        acct = Account.from_key(normalized)
        checksum_address = Web3.to_checksum_address(acct.address)
        self._keys.private_key = normalized
        self._keys.address = checksum_address
        return checksum_address

    def set_public_address(self, address: str) -> str:
        checksum_address = Web3.to_checksum_address(address)
        self._keys.address = checksum_address
        return checksum_address

    @property
    def address(self) -> str:
        if not self._keys.address:
            raise RuntimeError("Public address is not configured.")
        return self._keys.address

    @property
    def private_key(self) -> str:
        if not self._keys.private_key:
            raise RuntimeError("Private key is not configured.")
        return self._keys.private_key

    def has_private_key(self) -> bool:
        return self._keys.private_key is not None

    def is_ready(self) -> bool:
        return self._keys.address is not None

    def clear(self) -> None:
        self._keys = WalletKeys()

    @staticmethod
    def _normalize_private_key(key: str) -> str:
        stripped = key.lower().replace("0x", "", 1)
        if len(stripped) != 64:
            raise ValueError("Private key must be 32 bytes (64 hex characters).")
        return "0x" + stripped


__all__ = ["KeyStore"]
