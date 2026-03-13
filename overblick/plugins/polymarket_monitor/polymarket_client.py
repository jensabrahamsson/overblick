"""
Polymarket API client.

Provides a clean interface to fetch market data from Polymarket's API
with proper error handling, rate limiting, and caching.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import aiohttp
from pydantic import ValidationError

from .models import MarketCategory, MarketStatus, PolymarketMarket
from overblick.core.exceptions import PluginError

logger = logging.getLogger(__name__)


class PolymarketAPIError(PluginError):
    """Base exception for Polymarket API errors."""

    pass


class RateLimitExceeded(PolymarketAPIError):
    """Raised when rate limit is exceeded."""

    pass


class PolymarketClient:
    """
    Async client for Polymarket API.

    Features:
    - Async HTTP requests with aiohttp
    - Rate limiting (respects API limits)
    - Response caching (configurable TTL)
    - Error handling with retries
    - Pydantic validation of responses
    """

    BASE_URL = "https://gamma-api.polymarket.com/"

    def __init__(self, session: aiohttp.ClientSession | None = None):
        """
        Initialize the Polymarket client.

        Args:
            session: Optional aiohttp session (creates new if None)
        """
        self._session = session
        self._rate_limit_semaphore = asyncio.Semaphore(5)  # Max concurrent requests
        self._cache: dict[str, tuple[datetime, Any]] = {}
        self._default_cache_ttl = timedelta(minutes=5)

    async def __aenter__(self):
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()

    async def _make_request(self, endpoint: str, params: dict | None = None) -> dict[str, Any]:
        """
        Make an authenticated request to the Polymarket API.

        Args:
            endpoint: API endpoint (e.g., "markets")
            params: Query parameters

        Returns:
            JSON response as dict

        Raises:
            PolymarketAPIError: On API errors
            RateLimitExceeded: On rate limit violations
        """
        url = urljoin(self.BASE_URL, endpoint)

        async with self._rate_limit_semaphore:
            try:
                async with self._session.get(url, params=params) as response:
                    if response.status == 429:
                        raise RateLimitExceeded("Rate limit exceeded")

                    response.raise_for_status()
                    return await response.json()

            except aiohttp.ClientError as e:
                logger.error(f"HTTP error fetching {endpoint}: {e}")
                raise PolymarketAPIError(f"HTTP error: {e}") from e
            except TimeoutError as e:
                logger.error(f"Timeout fetching {endpoint}")
                raise PolymarketAPIError(f"Timeout: {e}") from e

    def _get_cached(self, key: str) -> Any | None:
        """Get cached value if not expired."""
        if key in self._cache:
            cached_time, value = self._cache[key]
            if datetime.now() - cached_time < self._default_cache_ttl:
                return value
            else:
                del self._cache[key]
        return None

    def _set_cached(self, key: str, value: Any) -> None:
        """Set cached value with current timestamp."""
        self._cache[key] = (datetime.now(), value)

    async def get_all_markets(self, limit: int = 100, offset: int = 0) -> list[PolymarketMarket]:
        """
        Fetch all active markets from Polymarket.

        Args:
            limit: Maximum number of markets to fetch
            offset: Pagination offset

        Returns:
            List of validated PolymarketMarket objects
        """
        cache_key = f"markets_{limit}_{offset}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            data = await self._make_request(
                "markets", {"limit": limit, "offset": offset, "active": "true"}
            )

            markets = []
            for market_data in data.get("markets", []):
                try:
                    market = self._parse_market_data(market_data)
                    markets.append(market)
                except (ValidationError, KeyError) as e:
                    logger.warning(f"Failed to parse market {market_data.get('id')}: {e}")
                    continue

            self._set_cached(cache_key, markets)
            return markets

        except PolymarketAPIError as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    async def get_market_by_id(self, market_id: str) -> PolymarketMarket | None:
        """
        Fetch a specific market by ID.

        Args:
            market_id: Polymarket market ID

        Returns:
            PolymarketMarket object or None if not found
        """
        cache_key = f"market_{market_id}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            data = await self._make_request(f"markets/{market_id}")

            try:
                market = self._parse_market_data(data)
                self._set_cached(cache_key, market)
                return market
            except (ValidationError, KeyError) as e:
                logger.error(f"Failed to parse market {market_id}: {e}")
                return None

        except PolymarketAPIError as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            return None

    async def get_market_by_slug(self, slug: str) -> PolymarketMarket | None:
        """
        Fetch a market by URL slug.

        Args:
            slug: Market URL slug (e.g., "will-trump-win-2024")

        Returns:
            PolymarketMarket object or None if not found
        """
        cache_key = f"market_slug_{slug}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            data = await self._make_request(f"markets/slug/{slug}")

            try:
                market = self._parse_market_data(data)
                self._set_cached(cache_key, market)
                return market
            except (ValidationError, KeyError) as e:
                logger.error(f"Failed to parse market slug {slug}: {e}")
                return None

        except PolymarketAPIError as e:
            logger.error(f"Failed to fetch market slug {slug}: {e}")
            return None

    async def get_market_ticker(self, market_id: str) -> dict[str, Any] | None:
        """
        Fetch real-time ticker data for a market.

        Args:
            market_id: Polymarket market ID

        Returns:
            Ticker data dict or None
        """
        cache_key = f"ticker_{market_id}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            data = await self._make_request(f"markets/{market_id}/ticker")
            self._set_cached(cache_key, data)
            return data
        except PolymarketAPIError as e:
            logger.error(f"Failed to fetch ticker for {market_id}: {e}")
            return None

    def _parse_market_data(self, data: dict[str, Any]) -> PolymarketMarket:
        """
        Parse raw API data into a PolymarketMarket object.

        Args:
            data: Raw market data from API

        Returns:
            Validated PolymarketMarket object

        Raises:
            ValidationError: If data doesn't match expected schema
            KeyError: If required fields are missing
        """
        # Parse basic fields
        market_id = data["id"]
        slug = data.get("slug", "")
        question = data.get("question", "")
        description = data.get("description")

        # Parse category
        category_str = data.get("category", "other").lower()
        try:
            category = MarketCategory(category_str)
        except ValueError:
            category = MarketCategory.OTHER

        # Parse status
        status_str = data.get("status", "open").lower()
        try:
            status = MarketStatus(status_str)
        except ValueError:
            status = MarketStatus.OPEN

        # Parse timestamps
        created_time = datetime.fromisoformat(data.get("createdTime", "").replace("Z", "+00:00"))

        end_time = None
        if data.get("endTime"):
            end_time = datetime.fromisoformat(data.get("endTime", "").replace("Z", "+00:00"))

        # Parse outcomes
        outcomes = []
        for outcome_data in data.get("outcomes", []):
            outcome = {
                "name": outcome_data.get("name", ""),
                "ticker": outcome_data.get("ticker", ""),
                "price": float(outcome_data.get("price", 0.0)),
                "volume_24h": float(outcome_data.get("volume24h", 0.0)),
                "last_updated": datetime.fromisoformat(
                    outcome_data.get("lastUpdated", "").replace("Z", "+00:00")
                )
                if outcome_data.get("lastUpdated")
                else datetime.now(),
            }
            outcomes.append(outcome)

        # Parse metrics
        volume_24h = float(data.get("volume24h", 0.0))
        liquidity = float(data.get("liquidity", 0.0))
        open_interest = float(data.get("openInterest", 0.0))

        # Calculate implied probability for binary markets
        implied_probability = None
        if len(outcomes) == 2:
            yes_outcome = next((o for o in outcomes if o["ticker"].upper() == "YES"), None)
            if yes_outcome:
                implied_probability = yes_outcome["price"]

        return PolymarketMarket(
            id=market_id,
            slug=slug,
            question=question,
            description=description,
            category=category,
            status=status,
            created_time=created_time,
            end_time=end_time,
            outcomes=outcomes,
            volume_24h=volume_24h,
            liquidity=liquidity,
            open_interest=open_interest,
            implied_probability=implied_probability,
        )
