"""
Simple Ethereum wallet for Polymarket betting.
Only supports sending ETH and ERC20 tokens.
"""

import logging
from decimal import Decimal
from typing import Optional

from eth_account import Account
from web3 import Web3

from .whallet_config import get_settings

logger = logging.getLogger(__name__)


class SimpleWallet:
    """Simple Ethereum wallet for sending transactions."""

    def __init__(self, rpc_url: str | None = None, private_key: str | None = None):
        self.settings = get_settings()
        self.rpc_url = rpc_url or self.settings.rpc_url
        self.web3 = Web3(Web3.HTTPProvider(self.rpc_url))

        self.account = None
        if private_key:
            self.set_private_key(private_key)
        elif self.settings.default_private_key:
            self.set_private_key(self.settings.default_private_key)

    def set_private_key(self, private_key: str):
        """Set private key for signing transactions."""
        # Remove 0x prefix if present
        if private_key.startswith("0x"):
            private_key = private_key[2:]

        if len(private_key) != 64:
            raise ValueError("Private key must be 64 hex characters")

        self.account = Account.from_key(private_key)
        logger.info(f"Wallet address: {self.account.address}")

    def get_nonce(self) -> int:
        """Get next nonce for the wallet address."""
        if not self.account:
            raise RuntimeError("Private key not configured")

        return self.web3.eth.get_transaction_count(self.account.address)

    def get_eth_balance(self, address: str | None = None) -> Decimal:
        """Get ETH balance in ether."""
        target_address = address or (self.account.address if self.account else None)
        if not target_address:
            raise RuntimeError("No address specified")

        balance_wei = self.web3.eth.get_balance(Web3.to_checksum_address(target_address))
        return Decimal(balance_wei) / Decimal(10**18)

    def send_eth(
        self,
        to_address: str,
        amount_eth: Decimal,
        gas_price_gwei: Decimal | None = None,
        nonce: int | None = None,
    ) -> str:
        """
        Send ETH to address.

        Args:
            to_address: Recipient address
            amount_eth: Amount in ETH
            gas_price_gwei: Gas price in Gwei (optional)
            nonce: Nonce (optional, will fetch if not provided)

        Returns:
            Transaction hash
        """
        if not self.account:
            raise RuntimeError("Private key not configured")

        # Convert amount to wei
        amount_wei = Web3.to_wei(float(amount_eth), "ether")

        # Build transaction
        tx = {
            "nonce": nonce or self.get_nonce(),
            "to": Web3.to_checksum_address(to_address),
            "value": amount_wei,
            "gas": 21000,  # Standard ETH transfer gas
            "chainId": self.settings.chain_id,
        }

        # Set gas price
        if gas_price_gwei is not None:
            tx["gasPrice"] = Web3.to_wei(float(gas_price_gwei), "gwei")
        else:
            tx["gasPrice"] = self.web3.eth.gas_price

        # Sign and send
        signed_tx = self.web3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.web3.eth.send_raw_transaction(signed_tx.raw_transaction)

        tx_hash_hex = tx_hash.hex()
        logger.info(f"Sent {amount_eth} ETH to {to_address[:10]}..., tx: {tx_hash_hex[:10]}...")

        return tx_hash_hex

    def send_erc20(
        self,
        token_address: str,
        to_address: str,
        amount: Decimal,
        gas_price_gwei: Decimal | None = None,
        nonce: int | None = None,
    ) -> str:
        """
        Send ERC20 token to address.

        Args:
            token_address: ERC20 token contract address
            to_address: Recipient address
            amount: Amount in token units (not wei)
            gas_price_gwei: Gas price in Gwei (optional)
            nonce: Nonce (optional, will fetch if not provided)

        Returns:
            Transaction hash
        """
        if not self.account:
            raise RuntimeError("Private key not configured")

        # ERC20 transfer function signature and encoding
        # transfer(address to, uint256 value)
        transfer_function = "0xa9059cbb"

        # Encode parameters
        to_address_padded = Web3.to_checksum_address(to_address)[2:].rjust(64, "0")

        # Get token decimals
        token_contract = self.web3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=[
                {
                    "constant": True,
                    "inputs": [],
                    "name": "decimals",
                    "outputs": [{"name": "", "type": "uint8"}],
                    "type": "function",
                }
            ],
        )

        try:
            decimals = token_contract.functions.decimals().call()
        except:
            # Default to 18 decimals if not available
            decimals = 18

        amount_int = int(amount * Decimal(10**decimals))
        amount_hex = hex(amount_int)[2:].rjust(64, "0")

        # Build data field
        data = transfer_function + to_address_padded + amount_hex

        # Estimate gas
        try:
            gas_estimate = self.web3.eth.estimate_gas(
                {
                    "from": self.account.address,
                    "to": Web3.to_checksum_address(token_address),
                    "data": data,
                }
            )
            gas = gas_estimate
        except:
            # Default gas for token transfer
            gas = 65000

        # Build transaction
        tx = {
            "nonce": nonce or self.get_nonce(),
            "to": Web3.to_checksum_address(token_address),
            "value": 0,
            "data": data,
            "gas": gas,
            "chainId": self.settings.chain_id,
        }

        # Set gas price
        if gas_price_gwei is not None:
            tx["gasPrice"] = Web3.to_wei(float(gas_price_gwei), "gwei")
        else:
            tx["gasPrice"] = self.web3.eth.gas_price

        # Sign and send
        signed_tx = self.web3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.web3.eth.send_raw_transaction(signed_tx.raw_transaction)

        tx_hash_hex = tx_hash.hex()
        logger.info(f"Sent {amount} tokens to {to_address[:10]}..., tx: {tx_hash_hex[:10]}...")

        return tx_hash_hex

    def wait_for_transaction(self, tx_hash: str, timeout: int = 120) -> dict:
        """
        Wait for transaction receipt.

        Args:
            tx_hash: Transaction hash
            timeout: Timeout in seconds

        Returns:
            Transaction receipt
        """
        return self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
