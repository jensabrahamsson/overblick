"""
Moltbook API Request Proxy.

Centralized request management to prevent API spamming and rate limit issues.
All Moltbook API calls should go through this proxy.

Features:
- Global rate limiting across all requests
- Automatic retry-after header handling
- Request caching to prevent duplicate calls
- Request counting and monitoring
- Graceful backoff on rate limit errors
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from collections import defaultdict
import hashlib
import json

logger = logging.getLogger(__name__)


class RequestCache:
    """Simple TTL-based request cache."""

    def __init__(self, ttl_seconds: int = 60):
        """
        Initialize cache.

        Args:
            ttl_seconds: Time-to-live for cached entries
        """
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._ttl = ttl_seconds

    def _make_key(self, method: str, endpoint: str, params: Optional[dict] = None) -> str:
        """Create cache key from request parameters."""
        key_parts = [method, endpoint]
        if params:
            # Sort params for consistent hashing
            key_parts.append(json.dumps(params, sort_keys=True))
        return hashlib.md5("".join(key_parts).encode()).hexdigest()

    def get(self, method: str, endpoint: str, params: Optional[dict] = None) -> Optional[Any]:
        """Get cached response if not expired."""
        key = self._make_key(method, endpoint, params)
        if key in self._cache:
            value, expires_at = self._cache[key]
            if time.time() < expires_at:
                logger.debug(f"Cache HIT: {method} {endpoint}")
                return value
            else:
                # Expired, remove
                del self._cache[key]

        logger.debug(f"Cache MISS: {method} {endpoint}")
        return None

    def set(self, method: str, endpoint: str, value: Any, params: Optional[dict] = None) -> None:
        """Store response in cache."""
        key = self._make_key(method, endpoint, params)
        expires_at = time.time() + self._ttl
        self._cache[key] = (value, expires_at)
        logger.debug(f"Cache SET: {method} {endpoint} (TTL: {self._ttl}s)")

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        logger.info("Cache cleared")


class MoltbookRequestProxy:
    """
    Proxy for all Moltbook API requests.

    Prevents spamming by:
    - Rate limiting requests globally
    - Respecting Retry-After headers
    - Caching identical requests
    - Tracking request metrics
    """

    def __init__(
        self,
        max_requests_per_minute: int = 10,  # Very conservative limit
        cache_ttl_seconds: int = 60,
        enable_cache: bool = True,
    ):
        """
        Initialize request proxy.

        Args:
            max_requests_per_minute: Global rate limit (conservative)
            cache_ttl_seconds: Cache TTL for GET requests
            enable_cache: Whether to enable caching
        """
        self._max_rpm = max_requests_per_minute
        self._request_times: list[float] = []
        self._rate_limit_until: Optional[datetime] = None

        # Cache (only for GET requests)
        self._cache_enabled = enable_cache
        self._cache = RequestCache(ttl_seconds=cache_ttl_seconds)

        # Metrics
        self._total_requests = 0
        self._cached_requests = 0
        self._rate_limited_requests = 0

        logger.info(
            f"MoltbookRequestProxy initialized: "
            f"{max_requests_per_minute} req/min, "
            f"cache={'enabled' if enable_cache else 'disabled'}"
        )

    async def wait_for_rate_limit(self) -> None:
        """Wait if we're currently rate limited (public method)."""
        if self._rate_limit_until:
            now = datetime.now()
            if now < self._rate_limit_until:
                wait_seconds = (self._rate_limit_until - now).total_seconds()
                logger.warning(
                    f"RATE LIMIT: Waiting {wait_seconds:.0f}s until {self._rate_limit_until}"
                )
                await asyncio.sleep(wait_seconds)
                self._rate_limit_until = None

    async def check_rate_limit(self) -> bool:
        """
        Check if we can make a request without exceeding rate limit (public method).

        Returns:
            True if request can proceed, False if rate limited
        """
        now = time.time()

        # Remove requests older than 1 minute
        cutoff = now - 60
        self._request_times = [t for t in self._request_times if t > cutoff]

        # Check if we're at limit
        if len(self._request_times) >= self._max_rpm:
            # Calculate wait time until oldest request expires
            oldest = self._request_times[0]
            wait_time = 60 - (now - oldest)

            logger.warning(
                f"Rate limit reached: {len(self._request_times)}/{self._max_rpm} "
                f"requests in last minute. Waiting {wait_time:.1f}s"
            )

            await asyncio.sleep(wait_time + 0.1)  # Add small buffer
            return await self.check_rate_limit()  # Recheck

        return True

    async def request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[dict] = None,
        params: Optional[dict] = None,
        skip_cache: bool = False,
    ) -> dict:
        """
        Make a proxied request to Moltbook API.

        This method should be called by MoltbookClient instead of direct HTTP calls.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            json_data: JSON body for POST/PUT
            params: Query parameters
            skip_cache: Force skip cache even for GET requests

        Returns:
            Response dict

        Raises:
            Exception: On request failure
        """
        # Wait for any active rate limit
        await self.wait_for_rate_limit()

        # Check cache for GET requests
        if method == "GET" and self._cache_enabled and not skip_cache:
            cached = self._cache.get(method, endpoint, params)
            if cached is not None:
                self._cached_requests += 1
                return cached

        # Check rate limit
        await self.check_rate_limit()

        # Record request time
        self._request_times.append(time.time())
        self._total_requests += 1

        # Log request
        logger.debug(
            f"PROXY REQUEST [{len(self._request_times)}/{self._max_rpm}]: "
            f"{method} {endpoint}"
        )

        # This will be implemented by integrating with MoltbookClient
        # For now, return a marker to show this needs integration
        return {
            "_proxy_marker": True,
            "method": method,
            "endpoint": endpoint,
            "json": json_data,
            "params": params,
        }

    def handle_rate_limit_response(self, retry_after_seconds: int) -> None:
        """
        Handle rate limit response from API.

        Args:
            retry_after_seconds: Seconds to wait before retrying
        """
        self._rate_limit_until = datetime.now() + timedelta(seconds=retry_after_seconds)
        self._rate_limited_requests += 1

        logger.warning(
            f"API RATE LIMIT: Blocking requests until {self._rate_limit_until} "
            f"({retry_after_seconds}s)"
        )

    def cache_response(
        self,
        method: str,
        endpoint: str,
        response: dict,
        params: Optional[dict] = None,
    ) -> None:
        """
        Cache a successful response.

        Args:
            method: HTTP method
            endpoint: API endpoint
            response: Response to cache
            params: Query parameters used
        """
        if method == "GET" and self._cache_enabled:
            self._cache.set(method, endpoint, response, params)

    def get_stats(self) -> dict:
        """Get proxy statistics."""
        cache_hit_rate = 0.0
        if self._total_requests > 0:
            cache_hit_rate = (self._cached_requests / self._total_requests) * 100

        return {
            "total_requests": self._total_requests,
            "cached_requests": self._cached_requests,
            "cache_hit_rate": f"{cache_hit_rate:.1f}%",
            "rate_limited_count": self._rate_limited_requests,
            "current_rpm": len(self._request_times),
            "max_rpm": self._max_rpm,
            "rate_limited_until": self._rate_limit_until.isoformat() if self._rate_limit_until else None,
        }

    def clear_cache(self) -> None:
        """Clear response cache."""
        self._cache.clear()
