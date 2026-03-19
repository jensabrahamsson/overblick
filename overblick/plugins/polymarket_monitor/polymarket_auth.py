"""
Polymarket Authentication Module.

Implements L1 and L2 authentication for Polymarket CLOB API as per official documentation:
https://docs.polymarket.com/api-reference/authentication.md

Supports:
- L1: EIP-712 signing with private key (for creating API credentials)
- L2: HMAC-SHA256 signing with API credentials (for trading)
- Automatic credential management
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Dict, Optional, Tuple
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

logger = logging.getLogger(__name__)


class PolymarketAuthError(Exception):
    """Base exception for Polymarket authentication errors."""

    pass


class PolymarketAuthenticator:
    """Handles Polymarket CLOB API authentication (L1 and L2)."""

    # CLOB API endpoints
    CLOB_BASE_URL = "https://clob.polymarket.com"
    CREATE_API_KEY_URL = f"{CLOB_BASE_URL}/auth/api-key"
    DERIVE_API_KEY_URL = f"{CLOB_BASE_URL}/auth/derive-api-key"

    # Polygon mainnet chain ID
    POLYGON_CHAIN_ID = 137

    def __init__(self, private_key: str, address: Optional[str] = None):
        """
        Initialize authenticator with private key.

        Args:
            private_key: Ethereum private key (hex string with 0x prefix)
            address: Optional Ethereum address (derived from private key if not provided)
        """
        self.private_key = private_key
        self.account = Account.from_key(private_key)
        self.address = address or self.account.address

        # API credentials (set after authentication)
        self.api_key: Optional[str] = None
        self.secret: Optional[str] = None
        self.passphrase: Optional[str] = None

        # Nonce for L1 authentication (default 0)
        self.nonce = 0

        logger.debug(f"Initialized PolymarketAuthenticator for address {self.address}")

    def _get_server_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        return datetime.utcnow().isoformat() + "Z"

    def _create_eip712_signature(self, timestamp: str, nonce: int = 0) -> str:
        """
        Create EIP-712 signature for L1 authentication.

        As per Polymarket documentation:
        https://docs.polymarket.com/api-reference/authentication.md

        Returns:
            Hex-encoded signature string
        """
        domain = {
            "name": "ClobAuthDomain",
            "version": "1",
            "chainId": self.POLYGON_CHAIN_ID,
        }

        types = {
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
                {"name": "message", "type": "string"},
            ]
        }

        value = {
            "address": self.address,
            "timestamp": timestamp,
            "nonce": nonce,
            "message": "This message attests that I control the given wallet",
        }

        # Encode and sign typed data
        encoded_message = encode_typed_data(
            full_message={
                "types": types,
                "domain": domain,
                "primaryType": "ClobAuth",
                "message": value,
            }
        )

        signed_message = self.account.sign_message(encoded_message)
        return signed_message.signature.hex()

    def _create_hmac_signature(
        self, method: str, path: str, body: str = "", timestamp: str = None
    ) -> str:
        """
        Create HMAC-SHA256 signature for L2 authentication.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API endpoint path
            body: Request body (empty string for GET requests)
            timestamp: Current timestamp

        Returns:
            Base64-encoded HMAC signature
        """
        if not self.secret:
            raise PolymarketAuthError("API secret not set. Call authenticate() first.")

        if timestamp is None:
            timestamp = str(int(time.time()))

        # Create message to sign
        message = timestamp + method.upper() + path + body

        # Decode base64 secret
        secret_bytes = base64.b64decode(self.secret)

        # Create HMAC signature
        signature = hmac.new(secret_bytes, message.encode(), hashlib.sha256).digest()

        # Return base64-encoded signature
        return base64.b64encode(signature).decode()

    async def create_api_credentials(self, nonce: int = 0) -> Dict[str, str]:
        """
        Create new API credentials using L1 authentication.

        Args:
            nonce: Nonce value (default 0, increment for new credentials)

        Returns:
            Dictionary with apiKey, secret, passphrase
        """
        import aiohttp

        timestamp = self._get_server_timestamp()
        signature = self._create_eip712_signature(timestamp, nonce)

        headers = {
            "POLY_ADDRESS": self.address,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": timestamp,
            "POLY_NONCE": str(nonce),
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(self.CREATE_API_KEY_URL, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise PolymarketAuthError(
                        f"Failed to create API credentials: {response.status} - {error_text}"
                    )

                data = await response.json()

                # Store credentials
                self.api_key = data["apiKey"]
                self.secret = data["secret"]
                self.passphrase = data["passphrase"]
                self.nonce = nonce

                logger.info(f"Created new API credentials for address {self.address}")
                return data

    async def derive_api_credentials(self, nonce: int = 0) -> Dict[str, str]:
        """
        Derive existing API credentials using L1 authentication.

        Args:
            nonce: Nonce value used when credentials were created

        Returns:
            Dictionary with apiKey, secret, passphrase
        """
        import aiohttp

        timestamp = self._get_server_timestamp()
        signature = self._create_eip712_signature(timestamp, nonce)

        headers = {
            "POLY_ADDRESS": self.address,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": timestamp,
            "POLY_NONCE": str(nonce),
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(self.DERIVE_API_KEY_URL, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise PolymarketAuthError(
                        f"Failed to derive API credentials: {response.status} - {error_text}"
                    )

                data = await response.json()

                # Store credentials
                self.api_key = data["apiKey"]
                self.secret = data["secret"]
                self.passphrase = data["passphrase"]
                self.nonce = nonce

                logger.info(f"Derived existing API credentials for address {self.address}")
                return data

    async def authenticate(self, nonce: int = 0) -> bool:
        """
        Authenticate with Polymarket API (try derive first, then create).

        Args:
            nonce: Nonce value to use

        Returns:
            True if authentication successful
        """
        try:
            # Try to derive existing credentials first
            await self.derive_api_credentials(nonce)
            logger.info(f"Successfully derived API credentials (nonce: {nonce})")
            return True
        except PolymarketAuthError as e:
            if "NOT_FOUND" in str(e) or "404" in str(e):
                # Credentials don't exist, create new ones
                logger.info(f"No existing credentials found, creating new ones (nonce: {nonce})")
                await self.create_api_credentials(nonce)
                return True
            else:
                # Other error
                logger.error(f"Failed to authenticate: {e}")
                raise

    def get_l2_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        """
        Get L2 authentication headers for API request.

        Args:
            method: HTTP method
            path: API endpoint path
            body: Request body (JSON string)

        Returns:
            Dictionary with L2 authentication headers
        """
        if not all([self.api_key, self.secret, self.passphrase]):
            raise PolymarketAuthError("API credentials not set. Call authenticate() first.")

        timestamp = str(int(time.time()))
        signature = self._create_hmac_signature(method, path, body, timestamp)

        return {
            "POLY_ADDRESS": self.address,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": timestamp,
            "POLY_API_KEY": self.api_key,
            "POLY_PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }

    def save_credentials(self, filepath: str) -> None:
        """Save API credentials to file."""
        if not all([self.api_key, self.secret, self.passphrase]):
            raise PolymarketAuthError("No credentials to save")

        credentials = {
            "address": self.address,
            "apiKey": self.api_key,
            "secret": self.secret,
            "passphrase": self.passphrase,
            "nonce": self.nonce,
            "saved_at": datetime.utcnow().isoformat(),
        }

        with open(filepath, "w") as f:
            json.dump(credentials, f, indent=2)

        logger.info(f"Saved credentials to {filepath}")

    def load_credentials(self, filepath: str) -> None:
        """Load API credentials from file."""
        with open(filepath, "r") as f:
            credentials = json.load(f)

        # Verify address matches
        if credentials["address"].lower() != self.address.lower():
            raise PolymarketAuthError(
                f"Address mismatch: file has {credentials['address']}, "
                f"authenticator has {self.address}"
            )

        self.api_key = credentials["apiKey"]
        self.secret = credentials["secret"]
        self.passphrase = credentials["passphrase"]
        self.nonce = credentials.get("nonce", 0)

        logger.info(
            f"Loaded credentials from {filepath} (saved: {credentials.get('saved_at', 'unknown')})"
        )


class PolymarketTradingClient:
    """Simplified trading client for Polymarket CLOB API."""

    def __init__(
        self,
        authenticator: PolymarketAuthenticator,
        signature_type: int = 1,
        funder: Optional[str] = None,
    ):
        """
        Initialize trading client.

        Args:
            authenticator: PolymarketAuthenticator instance
            signature_type: 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE (default: 1)
            funder: Funder address (default: authenticator address)
        """
        self.authenticator = authenticator
        self.signature_type = signature_type
        self.funder = funder or authenticator.address
        self.base_url = "https://clob.polymarket.com"

        logger.debug(
            f"Initialized PolymarketTradingClient (signature_type: {signature_type}, funder: {self.funder})"
        )

    async def place_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,  # "BUY" or "SELL"
        tick_size: str = "0.01",
        neg_risk: bool = False,
    ) -> Dict:
        """
        Place an order on Polymarket.

        Args:
            token_id: Polymarket token ID
            price: Order price (0.00-1.00)
            size: Order size in tokens
            side: "BUY" or "SELL"
            tick_size: Minimum price increment
            neg_risk: Whether this is a negative risk market

        Returns:
            Order response from API
        """
        import aiohttp

        # Validate inputs
        if side.upper() not in ["BUY", "SELL"]:
            raise ValueError("side must be 'BUY' or 'SELL'")

        if not 0 <= price <= 1:
            raise ValueError("price must be between 0 and 1")

        # Create order payload
        order_payload = {
            "tokenID": token_id,
            "price": str(price),
            "size": str(size),
            "side": side.upper(),
            "tickSize": tick_size,
            "negRisk": neg_risk,
            "signatureType": self.signature_type,
            "funder": self.funder,
        }

        body = json.dumps(order_payload)
        path = "/orders"

        # Get authentication headers
        headers = self.authenticator.get_l2_headers("POST", path, body)

        # Add order-specific headers
        headers["POLY_SIGNATURE_TYPE"] = str(self.signature_type)
        headers["POLY_FUNDER"] = self.funder

        url = self.base_url + path

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, data=body) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise PolymarketAuthError(
                        f"Failed to place order: {response.status} - {error_text}"
                    )

                return await response.json()

    async def cancel_order(self, order_id: str) -> Dict:
        """Cancel an order by ID."""
        import aiohttp

        path = f"/orders/{order_id}"
        headers = self.authenticator.get_l2_headers("DELETE", path)

        url = self.base_url + path

        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise PolymarketAuthError(
                        f"Failed to cancel order: {response.status} - {error_text}"
                    )

                return await response.json()

    async def get_open_orders(self, limit: int = 100, offset: int = 0) -> Dict:
        """Get open orders for authenticated user."""
        import aiohttp

        path = f"/orders?limit={limit}&offset={offset}"
        headers = self.authenticator.get_l2_headers("GET", path)

        url = self.base_url + path

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise PolymarketAuthError(
                        f"Failed to get orders: {response.status} - {error_text}"
                    )

                return await response.json()

    async def get_positions(self) -> Dict:
        """Get current positions for authenticated user."""
        import aiohttp

        path = "/positions"
        headers = self.authenticator.get_l2_headers("GET", path)

        url = self.base_url + path

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise PolymarketAuthError(
                        f"Failed to get positions: {response.status} - {error_text}"
                    )

                return await response.json()


# Helper function for testing
async def test_authentication():
    """Test Polymarket authentication flow."""
    import os

    # Get private key from environment
    private_key = os.getenv("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        print("Error: Set POLYMARKET_PRIVATE_KEY environment variable")
        return False

    try:
        print("1. Initializing authenticator...")
        auth = PolymarketAuthenticator(private_key)

        print("2. Authenticating (nonce: 0)...")
        await auth.authenticate(nonce=0)

        print(f"   API Key: {auth.api_key[:8]}...")
        print(f"   Passphrase: {auth.passphrase[:8]}...")
        print(f"   Secret: {auth.secret[:8]}...")

        print("3. Testing L2 headers generation...")
        headers = auth.get_l2_headers("GET", "/orders")
        print(f"   Generated {len(headers)} L2 headers")

        print("4. Initializing trading client...")
        client = PolymarketTradingClient(auth, signature_type=1)

        print("✅ Authentication test successful!")
        print(f"   Address: {auth.address}")
        print(f"   Ready for trading: Yes")

        return True

    except Exception as e:
        print(f"❌ Authentication test failed: {e}")
        return False


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_authentication())
