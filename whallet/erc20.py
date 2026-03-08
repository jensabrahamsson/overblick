from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict

from web3 import Web3
from web3.contract import Contract

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
]


class ERC20Token:
    """Thin wrapper around an ERC-20 contract."""

    def __init__(self, web3: Web3, address: str) -> None:
        self.web3 = web3
        self.address = Web3.to_checksum_address(address)
        self.contract = self._get_contract(self.web3, self.address)

    def decimals(self) -> int:
        return self.contract.functions.decimals().call()

    def symbol(self) -> str:
        try:
            return self.contract.functions.symbol().call()
        except Exception:  # pragma: no cover - some tokens revert on symbol
            return "UNKNOWN"

    def balance_of(self, account: str) -> int:
        """Get token balance for an account."""
        return self.contract.functions.balanceOf(account).call()

    def allowance(self, owner: str, spender: str) -> int:
        return self.contract.functions.allowance(owner, spender).call()

    def create_approve_transaction(
        self,
        owner: str,
        spender: str,
        amount: int,
        gas_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        txn = self.contract.functions.approve(spender, amount).build_transaction(
            {
                "from": owner,
                **gas_params,
            }
        )
        return txn

    @staticmethod
    @lru_cache(maxsize=512)
    def _get_contract(web3: Web3, address: str) -> Contract:
        return web3.eth.contract(address=address, abi=ERC20_ABI)


__all__ = ["ERC20Token", "ERC20_ABI"]
