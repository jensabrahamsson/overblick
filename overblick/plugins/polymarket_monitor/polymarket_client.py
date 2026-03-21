"""
Polymarket API client for gamma-api.polymarket.com.

Provides a clean interface to fetch market data from Polymarket's public API
with proper error handling, rate limiting, and caching. Compatible with gamma API format.
"""

import asyncio
import json
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
    """Async client for Polymarket gamma-api."""

    BASE_URL = "https://gamma-api.polymarket.com/"

    def __init__(self, session: aiohttp.ClientSession | None = None):
        self._session = session
        self._rate_limit_semaphore = asyncio.Semaphore(5)
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
        url = urljoin(self.BASE_URL, endpoint)

        async with self._rate_limit_semaphore:
            try:
                if self._session is None:
                    self._session = aiohttp.ClientSession()
                async with self._session.get(url, params=params) as response:
                    if response.status == 429:
                        raise RateLimitExceeded("Rate limit exceeded")

                    response.raise_for_status()
                    return await response.json()

            except aiohttp.ClientError as e:
                logger.error(f"HTTP error fetching {endpoint}: {e}")
                raise PolymarketAPIError(f"HTTP error: {e}") from e

    def _get_cached(self, key: str) -> Any | None:
        if key in self._cache:
            cached_time, value = self._cache[key]
            if datetime.now() - cached_time < self._default_cache_ttl:
                return value
            else:
                del self._cache[key]
        return None

    def _set_cached(self, key: str, value: Any) -> None:
        self._cache[key] = (datetime.now(), value)

    async def get_all_markets(
        self, limit: int = 100, offset: int = 0, use_mock_if_old: bool = True
    ) -> list[PolymarketMarket]:
        """Fetch markets from API, with fallback to mock data if API returns only old markets."""
        cache_key = f"markets_{limit}_{offset}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            data = await self._make_request("markets", {
                "limit": limit,
                "active": "true",
                "order": "volume24hr",
                "ascending": "false",
            })

            # Gamma API returns array directly
            market_list = data if isinstance(data, list) else []

            markets = []
            for market_data in market_list:
                if len(markets) >= limit:
                    break
                if not isinstance(market_data, dict):
                    continue
                try:
                    market = self._parse_market_data(market_data)
                    markets.append(market)
                except (ValidationError, KeyError, Exception) as e:
                    market_id = (
                        market_data.get("id", "unknown")
                        if isinstance(market_data, dict)
                        else "unknown"
                    )
                    logger.warning(f"Failed to parse market {market_id}: {e}")
                    continue

            # Check if majority of markets are too old (pre-2024)
            if use_mock_if_old and markets:
                recent_count = sum(
                    1 for m in markets if m.created_time and m.created_time.year >= 2024
                )
                if len(markets) > 0 and recent_count / len(markets) < 0.2:
                    logger.warning(
                        "API returned mostly old markets (%d/%d recent), using mock data",
                        recent_count,
                        len(markets),
                    )
                    return await self._get_mock_markets(limit)

            self._set_cached(cache_key, markets)
            return markets

        except PolymarketAPIError as e:
            logger.error(f"Failed to fetch markets: {e}")
            # Fall back to mock data
            return await self._get_mock_markets(limit)

    async def _get_mock_markets(self, limit: int = 100) -> list[PolymarketMarket]:
        """Generate modern mock markets for testing."""
        try:
            from .mock_data_generator import ModernMockDataGenerator

            generator = ModernMockDataGenerator()
            markets = generator.generate_markets(limit)
            logger.info(f"Generated {len(markets)} mock markets for testing")
            return markets
        except ImportError as e:
            logger.error(f"Failed to import mock data generator: {e}")
            return []

    async def get_market_by_id(self, market_id: str) -> PolymarketMarket | None:
        try:
            data = await self._make_request(f"markets")

            if isinstance(data, list):
                for m in data:
                    if isinstance(m, dict) and str(m.get("id", "")) == str(market_id):
                        return self._parse_market_data(m)

            return None

        except PolymarketAPIError as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            return None

    def _safe_float(self, val: Any, default: float = 0.0) -> float:
        try:
            return float(val) if val is not None else default
        except (ValueError, TypeError):
            return default

    def _safe_datetime(self, val: str | None) -> datetime | None:
        if not val or val == "":
            return None

        formats = [
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(val.replace("Z", ""), fmt)
            except ValueError:
                continue

        return None

    def _parse_market_data(self, data: dict[str, Any]) -> PolymarketMarket:
        # Parse basic fields (gamma API uses snake_case sometimes, camelCase others)
        market_id = str(data.get("id", ""))
        slug = data.get("slug", "")
        question = data.get("question", "")
        description = data.get("description") or data.get("summary")

        # Parse category
        raw_category = (data.get("category") or "other").lower().replace("-", "_")
        try:
            category = MarketCategory(raw_category)
        except ValueError:
            category = MarketCategory.OTHER

        # Status - gamma API uses 'closed' boolean and 'archived'
        is_closed = data.get("closed", False) or data.get("archived", False)
        status = MarketStatus.CLOSED if is_closed else MarketStatus.OPEN

        # Parse timestamps (gamma API uses createdAt, endDate)
        created_time = self._safe_datetime(data.get("createdAt")) or datetime.now()

        end_time = self._safe_datetime(data.get("endDate") or data.get("endTime"))

        # Parse outcomes - gamma API returns JSON string like '["Yes", "No"]'
        outcomes_raw = data.get("outcomes", [])
        if isinstance(outcomes_raw, str):
            try:
                outcomes_list = json.loads(outcomes_raw)
            except (json.JSONDecodeError, TypeError):
                outcomes_list = []
        else:
            outcomes_list = list(outcomes_raw) if outcomes_raw else []

        # Parse outcomePrices - also JSON string
        prices_raw = data.get("outcomePrices", "[]")
        if isinstance(prices_raw, str):
            try:
                prices_list = json.loads(prices_raw)
            except (json.JSONDecodeError, TypeError):
                prices_list = []
        else:
            prices_list = list(prices_raw) if prices_raw else []

        # Build outcomes with correct prices
        outcomes = []
        for i, outcome_name in enumerate(outcomes_list):
            if isinstance(outcome_name, str):
                name = outcome_name
                ticker = "YES" if "Yes" in name or "Ja" in name else "NO"
            else:
                name = outcome_name.get("name", "")
                ticker = outcome_name.get("ticker", ("YES" if "Yes" in name else "NO"))

            # Get price for this outcome
            try:
                price = self._safe_float(prices_list[i]) if i < len(prices_list) else 0.5
            except (IndexError, TypeError):
                price = 0.5

            outcomes.append(
                {
                    "name": name,
                    "ticker": ticker,
                    "price": max(0.01, min(0.99, price)),  # Clamp to valid range
                    "volume_24h": 0.0,
                    "last_updated": datetime.now(),
                }
            )

        # Parse metrics (gamma API uses volumeNum, liquidityNum)
        volume_24h = self._safe_float(data.get("volumeNum") or data.get("volume", 0))
        liquidity = self._safe_float(data.get("liquidityNum") or data.get("liquidity", 0))
        open_interest = self._safe_float(data.get("openInterest", volume_24h / 2))

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
