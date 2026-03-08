"""
Ethereum RPC client for fetching token metadata and contract information.
Uses Infura HTTP endpoint for standard JSON-RPC calls.

Standalone version for Whallet - copied from ethereum_ng.
"""

import aiohttp
import asyncio
import logging
import random
import time
from typing import Dict, Optional, List
from collections import OrderedDict

logger = logging.getLogger(__name__)


class MetadataCache:
    """
    Persistent cache for immutable token metadata.

    Caches ONLY fields that never change after token deployment:
    - name, symbol, decimals, total_supply

    Does NOT cache volatile data:
    - liquidity_usd, price_usd, volume_24h, pair_address, etc.

    Uses 24-hour TTL to ensure freshness while reducing RPC calls.
    """

    # Only these fields are safe to cache (immutable after deployment)
    CACHEABLE_FIELDS = {'name', 'symbol', 'decimals', 'total_supply', 'address', 'source'}

    def __init__(self, ttl_hours: int = 24, max_size: int = 50000):
        """
        Initialize metadata cache.

        Args:
            ttl_hours: Time-to-live in hours (default: 24h)
            max_size: Maximum cache entries (default: 50k = ~10MB)
        """
        self.cache = OrderedDict()  # LRU cache
        self.ttl = ttl_hours * 3600  # Convert to seconds
        self.max_size = max_size
        self.hits = 0
        self.misses = 0

    def get(self, address: str) -> Optional[Dict]:
        """
        Get cached metadata if available and fresh.

        Args:
            address: Token contract address (case-insensitive)

        Returns:
            Cached metadata dict or None if expired/missing
        """
        key = address.lower()

        if key in self.cache:
            entry = self.cache[key]
            age = time.time() - entry['timestamp']

            if age < self.ttl:
                # Move to end (LRU)
                self.cache.move_to_end(key)
                self.hits += 1
                logger.debug(f"✅ Cache HIT for {address[:10]}... (age: {age/3600:.1f}h)")
                return entry['data'].copy()
            else:
                # Expired - remove
                del self.cache[key]
                logger.debug(f"⏰ Cache EXPIRED for {address[:10]}... (age: {age/3600:.1f}h)")

        self.misses += 1
        return None

    def set(self, address: str, metadata: Dict) -> None:
        """
        Cache metadata (only immutable fields).

        Args:
            address: Token contract address
            metadata: Full metadata dict (only cacheable fields will be stored)
        """
        key = address.lower()

        # Extract only cacheable (immutable) fields
        cached_data = {
            k: v for k, v in metadata.items()
            if k in self.CACHEABLE_FIELDS
        }

        # Only cache if we have useful data
        if cached_data.get('name') or cached_data.get('symbol') or cached_data.get('decimals') is not None:
            # Enforce max size (LRU eviction)
            if len(self.cache) >= self.max_size:
                # Remove oldest entry
                oldest_key = next(iter(self.cache))
                del self.cache[oldest_key]
                logger.debug(f"🗑️ Cache eviction (max size reached): {oldest_key[:10]}...")

            self.cache[key] = {
                'data': cached_data,
                'timestamp': time.time()
            }
            self.cache.move_to_end(key)  # Mark as recently used
            logger.debug(f"💾 Cached metadata for {address[:10]}...")

    def get_stats(self) -> Dict:
        """Get cache statistics."""
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0

        return {
            'size': len(self.cache),
            'max_size': self.max_size,
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate_percent': hit_rate,
            'ttl_hours': self.ttl / 3600
        }

class InfuraRPCClient:
    """
    Ethereum RPC client for interacting with Ethereum blockchain via Infura.
    Handles token metadata fetching, contract calls, and transaction processing.

    Standalone version for Whallet - does not depend on ethereum_ng.
    """

    def __init__(self, infura_api_key: str):
        """
        Initialize Infura RPC client.

        Args:
            infura_api_key: Infura API key for Ethereum mainnet access
        """
        self.infura_api_key = infura_api_key
        self.rpc_url = f"https://mainnet.infura.io/v3/{infura_api_key}"
        self.session = None
        self._rate_lock = asyncio.Lock()
        self._min_interval = 0.5  # 2 requests per second (conservative for Infura)
        self._last_call_ts = 0.0
        self._max_retries = 5
        self._base_backoff = 0.6  # seconds, tuned for Infura reliability
        self.metadata_cache = MetadataCache()  # 24h cache for immutable metadata

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def _make_rpc_call(self, method: str, params: List) -> Optional[Dict]:
        """Make a JSON-RPC call to Ethereum node."""
        if not self.session:
            self.session = aiohttp.ClientSession()

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": 1
        }

        for attempt in range(self._max_retries):
            backoff = self._base_backoff * (2 ** attempt)
            jitter = random.uniform(0.0, 0.3)
            await self._throttle()

            try:
                async with self.session.post(
                    self.rpc_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("result")

                    if response.status == 429:
                        # Rate limited - use exponential backoff
                        wait_time = backoff + jitter
                        logger.warning(
                            f"RPC rate limited (429, attempt {attempt + 1}/{self._max_retries}). "
                            f"Backing off {wait_time:.1f}s"
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    logger.error(f"RPC call failed with status {response.status}")
                    return None

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"RPC call failed (attempt {attempt + 1}/{self._max_retries}) for {method}: {e}"
                )
                await asyncio.sleep(backoff + jitter)

        logger.error(f"RPC call gave up after {self._max_retries} retries for method {method}")
        return None

    async def _throttle(self):
        async with self._rate_lock:
            now = asyncio.get_running_loop().time()
            wait = self._min_interval - (now - self._last_call_ts)
            if wait > 0:
                await asyncio.sleep(wait)
                now = asyncio.get_running_loop().time()
            self._last_call_ts = now

    async def get_transaction(self, tx_hash: str) -> Optional[Dict]:
        """Get transaction details by hash."""
        return await self._make_rpc_call("eth_getTransactionByHash", [tx_hash])

    async def get_transaction_receipt(self, tx_hash: str) -> Optional[Dict]:
        """Get transaction receipt by hash."""
        return await self._make_rpc_call("eth_getTransactionReceipt", [tx_hash])

    async def get_block_by_number(self, block_number: int, full_transactions: bool = False) -> Optional[Dict]:
        """Get block by number."""
        block_hex = hex(block_number)
        return await self._make_rpc_call("eth_getBlockByNumber", [block_hex, full_transactions])

    async def get_latest_block_number(self) -> Optional[int]:
        """Get the latest block number."""
        result = await self._make_rpc_call("eth_blockNumber", [])
        if result:
            return int(result, 16)
        return None

    async def get_code(self, address: str, block: str = "latest") -> Optional[str]:
        """
        Get the compiled byte code of a smart contract at the given address.

        Args:
            address: The contract address (20 bytes)
            block: Block number in hex (e.g., "0x65a8db") or tag ("latest", "earliest", "pending")

        Returns:
            The compiled byte code as a hex string, or None if error
        """
        return await self._make_rpc_call("eth_getCode", [address, block])

    async def call_contract(self, contract_address: str, data: str, block: str = "latest") -> Optional[str]:
        """Make a contract call."""
        call_data = {
            "to": contract_address,
            "data": data
        }
        return await self._make_rpc_call("eth_call", [call_data, block])

    async def get_erc20_token_info(self, contract_address: str) -> Dict:
        """
        Get ERC-20 token information by calling standard methods.
        Returns name, symbol, decimals, and total supply.
        """
        # Check cache first (24h TTL for immutable metadata)
        cached_metadata = self.metadata_cache.get(contract_address)
        if cached_metadata:
            logger.debug(f"🎯 Cache HIT for {contract_address[:10]}... - returning cached metadata")
            return cached_metadata

        token_info = {
            "address": contract_address,
            "name": None,
            "symbol": None,
            "decimals": None,
            "total_supply": None,
            "source": "ethereum_rpc"
        }

        try:
            # Standard ERC-20 method signatures
            name_sig = "0x06fdde03"      # name()
            symbol_sig = "0x95d89b41"    # symbol()
            decimals_sig = "0x313ce567"  # decimals()
            total_supply_sig = "0x18160ddd"  # totalSupply()

            # Make concurrent calls for efficiency
            tasks = [
                self.call_contract(contract_address, name_sig),
                self.call_contract(contract_address, symbol_sig),
                self.call_contract(contract_address, decimals_sig),
                self.call_contract(contract_address, total_supply_sig)
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Parse name
            if results[0] and not isinstance(results[0], Exception):
                token_info["name"] = self._decode_string(results[0])

            # Parse symbol
            if results[1] and not isinstance(results[1], Exception):
                token_info["symbol"] = self._decode_string(results[1])

            # Parse decimals
            if results[2] and not isinstance(results[2], Exception):
                try:
                    token_info["decimals"] = int(results[2], 16)
                except (ValueError, TypeError):
                    pass

            # Parse total supply
            if results[3] and not isinstance(results[3], Exception):
                try:
                    token_info["total_supply"] = str(int(results[3], 16))
                except (ValueError, TypeError):
                    pass

            # Validate this is a proper ERC-20 token (must have decimals)
            if (token_info["name"] or token_info["symbol"]) and token_info["decimals"] is not None:
                logger.info(f"✅ Retrieved ERC-20 token info for {contract_address}: {token_info['name']} ({token_info['symbol']}) - {token_info['decimals']} decimals")
                # Cache successful metadata (only immutable fields)
                self.metadata_cache.set(contract_address, token_info)
                return token_info
            else:
                logger.debug(f"❌ Not a valid ERC-20 token {contract_address} (missing decimals or metadata)")
                token_info["source"] = "not_erc20"
                return token_info

        except Exception as e:
            logger.error(f"Error getting token info for {contract_address}: {e}")
            token_info["source"] = "error"
            return token_info

    async def batch_get_erc20_metadata(self, contract_addresses: List[str], max_concurrent: int = 10) -> List[Dict]:
        """
        Batch fetch ERC-20 metadata for multiple tokens concurrently.

        Args:
            contract_addresses: List of token contract addresses
            max_concurrent: Maximum concurrent requests (default: 10)

        Returns:
            List of token metadata dicts (same order as input)
        """
        if not contract_addresses:
            return []

        logger.info(f"📦 Batch fetching metadata for {len(contract_addresses)} tokens")

        # Create tasks with semaphore to limit concurrency
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_with_semaphore(address: str) -> Dict:
            async with semaphore:
                try:
                    return await self.get_erc20_token_info(address)
                except Exception as e:
                    logger.error(f"Error fetching metadata for {address}: {e}")
                    return {
                        "address": address,
                        "name": None,
                        "symbol": None,
                        "decimals": None,
                        "total_supply": None,
                        "source": "error"
                    }

        # Fetch all concurrently
        tasks = [fetch_with_semaphore(addr) for addr in contract_addresses]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions and convert to list
        metadata_list = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Exception fetching {contract_addresses[i]}: {result}")
                metadata_list.append({
                    "address": contract_addresses[i],
                    "name": None,
                    "symbol": None,
                    "decimals": None,
                    "total_supply": None,
                    "source": "exception"
                })
            else:
                metadata_list.append(result)

        successful = sum(1 for m in metadata_list if m.get("source") == "ethereum_rpc")
        logger.info(f"✅ Batch metadata fetch complete: {successful}/{len(contract_addresses)} successful")

        return metadata_list

    def _decode_string(self, hex_data: str) -> Optional[str]:
        """
        Decode hex-encoded string from contract call.
        Handles both fixed-length and dynamic string encoding.
        """
        try:
            if not hex_data or hex_data == "0x":
                return None

            # Remove 0x prefix
            hex_data = hex_data[2:] if hex_data.startswith("0x") else hex_data

            # For dynamic strings, skip the first 64 chars (offset and length)
            if len(hex_data) > 128:
                # Skip offset (32 bytes) and get length
                length_hex = hex_data[64:128]
                length = int(length_hex, 16)

                # Extract the actual string data
                string_hex = hex_data[128:128 + (length * 2)]

                # Convert to bytes and decode
                string_bytes = bytes.fromhex(string_hex)
                return string_bytes.decode('utf-8', errors='ignore').strip('\x00')
            else:
                # Simple hex to string conversion
                string_bytes = bytes.fromhex(hex_data)
                return string_bytes.decode('utf-8', errors='ignore').strip('\x00')

        except Exception as e:
            logger.debug(f"Failed to decode string from {hex_data}: {e}")
            return None

    async def analyze_transaction_for_token_creation(self, tx_hash: str) -> Optional[Dict]:
        """
        Analyze a transaction to determine if it created a new ERC-20 token.
        Returns token creation details if found.
        """
        try:
            # Get transaction and receipt
            tx_data = await self.get_transaction(tx_hash)
            tx_receipt = await self.get_transaction_receipt(tx_hash)

            if not tx_data or not tx_receipt:
                return None

            # Check if transaction created a contract
            if tx_receipt.get("contractAddress"):
                contract_address = tx_receipt["contractAddress"]

                # Try to get ERC-20 token info
                token_info = await self.get_erc20_token_info(contract_address)

                if token_info["name"] or token_info["symbol"]:
                    return {
                        "contract_address": contract_address,
                        "transaction_hash": tx_hash,
                        "block_number": int(tx_receipt["blockNumber"], 16),
                        "creator": tx_data.get("from"),
                        "token_info": token_info,
                        "gas_used": int(tx_receipt["gasUsed"], 16),
                        "status": int(tx_receipt["status"], 16)
                    }

            return None

        except Exception as e:
            logger.error(f"Error analyzing transaction {tx_hash}: {e}")
            return None

    async def get_logs(self, from_block: int, to_block: int, address: str = None, topics: List[str] = None) -> List[Dict]:
        """Get logs for specified block range and filters."""
        params = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block)
        }

        if address:
            params["address"] = address

        if topics:
            params["topics"] = topics

        result = await self._make_rpc_call("eth_getLogs", [params])
        return result if result else []

    async def close(self):
        """Close the HTTP session."""
        if self.session:
            await self.session.close()
