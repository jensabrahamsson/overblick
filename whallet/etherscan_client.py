from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

import requests

try:
    from .whallet_config import get_settings
except ImportError:
    # Direct import mode (when whallet/ is in sys.path)
    from whallet_config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class TokenHolding:
    token_address: str
    symbol: str
    decimals: int
    raw_balance: int

    @property
    def normalized_balance(self) -> Decimal:
        if self.decimals == 0:
            return Decimal(self.raw_balance)
        return Decimal(self.raw_balance) / Decimal(10**self.decimals)


class EtherscanClient:
    """Minimal client for retrieving ERC-20 balances via Etherscan API V2."""

    BASE_URL = "https://api.etherscan.io/v2/api"
    CHAIN_ID = 1  # Ethereum mainnet
    RATE_LIMIT_DELAY = 0.5  # 500ms delay = 2 calls/sec (safe margin for parallel instances)

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.etherscan_api_key
        self._last_request_time = 0.0

    def get_eth_balance_wei(self, address: str) -> int:
        params = {
            "chainid": self.CHAIN_ID,
            "module": "account",
            "action": "balance",
            "address": address,
            "tag": "latest",
            "apikey": self.api_key,
        }
        response = self._request(params)
        return int(response["result"])

    def get_token_holdings(self, address: str) -> list[TokenHolding]:
        transfers = self._get_token_transfers(address)
        tokens = self._extract_unique_tokens(transfers)
        holdings: list[TokenHolding] = []
        for token in tokens.values():
            try:
                raw_balance = self._get_token_balance(address, token["contractAddress"])  # type: ignore[index]
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "Failed to fetch balance for token %s: %s",
                    token["contractAddress"],
                    exc,
                )
                continue
            holdings.append(
                TokenHolding(
                    token_address=token["contractAddress"],
                    symbol=token.get("tokenSymbol", "UNKNOWN"),
                    decimals=int(token.get("tokenDecimal", "18") or 18),
                    raw_balance=raw_balance,
                )
            )
        return holdings

    def _get_token_transfers(self, address: str) -> list[dict[str, str]]:
        params = {
            "chainid": self.CHAIN_ID,
            "module": "account",
            "action": "tokentx",
            "address": address,
            "sort": "asc",
            "page": 1,
            "offset": 500,
            "apikey": self.api_key,
        }
        response = self._request(params)
        result = response.get("result", [])
        if isinstance(result, list):
            return result
        return []

    def _get_token_balance(self, address: str, token_address: str) -> int:
        params = {
            "chainid": self.CHAIN_ID,
            "module": "account",
            "action": "tokenbalance",
            "contractaddress": token_address,
            "address": address,
            "tag": "latest",
            "apikey": self.api_key,
        }
        response = self._request(params)
        return int(response["result"])

    @staticmethod
    def _extract_unique_tokens(transfers: Iterable[dict[str, str]]) -> dict[str, dict[str, str]]:
        tokens: dict[str, dict[str, str]] = {}
        for transfer in transfers:
            contract = transfer.get("contractAddress")
            if not contract:
                continue
            if contract not in tokens:
                tokens[contract] = transfer
        return tokens

    def _request(self, params: dict[str, str]) -> dict[str, str]:
        # Rate limiting: ensure minimum delay between requests
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)

        try:
            resp = requests.get(self.BASE_URL, params=params, timeout=30)
            self._last_request_time = time.time()
        except requests.RequestException as exc:  # pragma: no cover - network failure path
            raise RuntimeError(f"Etherscan request failed: {exc}") from exc
        if resp.status_code != 200:
            raise RuntimeError(f"Etherscan responded with {resp.status_code}: {resp.text}")
        payload = resp.json()
        if payload.get("status") == "0" and payload.get("message") != "No transactions found":
            raise RuntimeError(f"Etherscan error: {payload.get('result', 'unknown error')}")
        return payload


__all__ = ["EtherscanClient", "TokenHolding"]
