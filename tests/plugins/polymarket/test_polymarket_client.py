"""Tests for polymarket_client — covers all uncovered lines."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.plugins.polymarket_monitor.polymarket_client import (
    PolymarketAPIError,
    PolymarketClient,
    RateLimitExceeded,
)


class TestClientInit:
    def test_should_initialize_with_session(self):
        session = MagicMock()
        client = PolymarketClient(session=session)
        assert client._session is session

    def test_should_initialize_without_session(self):
        client = PolymarketClient()
        assert client._session is None


class TestContextManager:
    @pytest.mark.asyncio
    async def test_should_create_session_on_enter(self):
        client = PolymarketClient()
        mock_session = MagicMock()
        mock_session.close = AsyncMock()
        with patch("overblick.plugins.polymarket_monitor.polymarket_client.aiohttp") as mock_aiohttp:
            mock_aiohttp.ClientSession.return_value = mock_session
            result = await client.__aenter__()
        assert result is client
        assert client._session is mock_session

    @pytest.mark.asyncio
    async def test_should_not_create_session_if_exists(self):
        session = MagicMock()
        client = PolymarketClient(session=session)
        result = await client.__aenter__()
        assert result is client
        assert client._session is session

    @pytest.mark.asyncio
    async def test_should_close_session_on_exit(self):
        session = AsyncMock()
        client = PolymarketClient(session=session)
        await client.__aexit__(None, None, None)
        session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_handle_no_session_on_exit(self):
        client = PolymarketClient(session=None)
        await client.__aexit__(None, None, None)
        # Should not raise


class TestMakeRequest:
    @pytest.mark.asyncio
    async def test_should_make_get_request(self):
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"data": "test"})
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        client = PolymarketClient(session=session)
        result = await client._make_request("markets")
        assert result == {"data": "test"}

    @pytest.mark.asyncio
    async def test_should_raise_on_rate_limit(self):
        mock_response = AsyncMock()
        mock_response.status = 429
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        client = PolymarketClient(session=session)
        with pytest.raises(RateLimitExceeded):
            await client._make_request("markets")

    @pytest.mark.asyncio
    async def test_should_raise_on_client_error(self):
        import aiohttp

        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.raise_for_status = MagicMock(
            side_effect=aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=500,
                message="Server Error",
            )
        )
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        client = PolymarketClient(session=session)
        with pytest.raises(PolymarketAPIError, match="HTTP error"):
            await client._make_request("markets")

    @pytest.mark.asyncio
    async def test_should_raise_on_timeout(self):
        mock_response = AsyncMock()
        mock_response.__aenter__ = AsyncMock(side_effect=TimeoutError("timeout"))
        mock_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.get = MagicMock(return_value=mock_response)

        client = PolymarketClient(session=session)
        with pytest.raises(PolymarketAPIError, match="Timeout"):
            await client._make_request("markets")


class TestCache:
    def test_should_return_cached_value(self):
        client = PolymarketClient()
        client._set_cached("key", "value")
        result = client._get_cached("key")
        assert result == "value"

    def test_should_return_none_for_expired_cache(self):
        client = PolymarketClient()
        client._cache["key"] = (datetime.now() - timedelta(hours=1), "old_value")
        result = client._get_cached("key")
        assert result is None
        assert "key" not in client._cache

    def test_should_return_none_for_missing_key(self):
        client = PolymarketClient()
        assert client._get_cached("missing") is None

    def test_should_set_cached_value(self):
        client = PolymarketClient()
        client._set_cached("k", "v")
        assert "k" in client._cache


class TestGetAllMarkets:
    @pytest.mark.asyncio
    async def test_should_return_cached_markets(self):
        client = PolymarketClient()
        cached_markets = [MagicMock()]
        client._set_cached("markets_100_0", cached_markets)
        result = await client.get_all_markets()
        assert result == cached_markets

    @pytest.mark.asyncio
    async def test_should_fetch_and_parse_markets(self):
        client = PolymarketClient(session=MagicMock())
        market_data = {
            "markets": [
                {
                    "id": "mkt1",
                    "slug": "test",
                    "question": "Q?",
                    "category": "politics",
                    "status": "open",
                    "createdTime": "2026-01-01T00:00:00Z",
                    "outcomes": [
                        {"name": "Yes", "ticker": "YES", "price": 0.6, "volume24h": 1000},
                        {"name": "No", "ticker": "NO", "price": 0.4, "volume24h": 500},
                    ],
                    "volume24h": 1500,
                    "liquidity": 5000,
                }
            ]
        }
        client._make_request = AsyncMock(return_value=market_data)
        result = await client.get_all_markets()
        assert len(result) == 1
        assert result[0].id == "mkt1"

    @pytest.mark.asyncio
    async def test_should_skip_invalid_market_data(self):
        client = PolymarketClient(session=MagicMock())
        market_data = {
            "markets": [
                {"invalid": "data"},  # Missing required fields
            ]
        }
        client._make_request = AsyncMock(return_value=market_data)
        result = await client.get_all_markets()
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_should_return_empty_on_api_error(self):
        client = PolymarketClient(session=MagicMock())
        client._make_request = AsyncMock(side_effect=PolymarketAPIError("err"))
        result = await client.get_all_markets()
        assert result == []


class TestGetMarketById:
    @pytest.mark.asyncio
    async def test_should_return_cached_market(self):
        client = PolymarketClient()
        market = MagicMock()
        client._set_cached("market_m1", market)
        result = await client.get_market_by_id("m1")
        assert result is market

    @pytest.mark.asyncio
    async def test_should_fetch_market(self):
        client = PolymarketClient(session=MagicMock())
        data = {
            "id": "m1",
            "slug": "test",
            "question": "Q?",
            "category": "politics",
            "status": "open",
            "createdTime": "2026-01-01T00:00:00Z",
            "outcomes": [],
            "volume24h": 1000,
            "liquidity": 5000,
        }
        client._make_request = AsyncMock(return_value=data)
        result = await client.get_market_by_id("m1")
        assert result.id == "m1"

    @pytest.mark.asyncio
    async def test_should_return_none_on_parse_error(self):
        client = PolymarketClient(session=MagicMock())
        client._make_request = AsyncMock(return_value={"invalid": True})
        result = await client.get_market_by_id("m1")
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_on_api_error(self):
        client = PolymarketClient(session=MagicMock())
        client._make_request = AsyncMock(side_effect=PolymarketAPIError("err"))
        result = await client.get_market_by_id("m1")
        assert result is None


class TestGetMarketBySlug:
    @pytest.mark.asyncio
    async def test_should_return_cached_market(self):
        client = PolymarketClient()
        market = MagicMock()
        client._set_cached("market_slug_test", market)
        result = await client.get_market_by_slug("test")
        assert result is market

    @pytest.mark.asyncio
    async def test_should_fetch_market_by_slug(self):
        client = PolymarketClient(session=MagicMock())
        data = {
            "id": "m1",
            "slug": "test",
            "question": "Q?",
            "category": "other",
            "status": "open",
            "createdTime": "2026-01-01T00:00:00Z",
            "outcomes": [],
            "volume24h": 1000,
        }
        client._make_request = AsyncMock(return_value=data)
        result = await client.get_market_by_slug("test")
        assert result.id == "m1"

    @pytest.mark.asyncio
    async def test_should_return_none_on_parse_error(self):
        client = PolymarketClient(session=MagicMock())
        client._make_request = AsyncMock(return_value={"bad": True})
        result = await client.get_market_by_slug("test")
        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_on_api_error(self):
        client = PolymarketClient(session=MagicMock())
        client._make_request = AsyncMock(side_effect=PolymarketAPIError("err"))
        result = await client.get_market_by_slug("test")
        assert result is None


class TestGetMarketTicker:
    @pytest.mark.asyncio
    async def test_should_return_cached_ticker(self):
        client = PolymarketClient()
        ticker = {"price": 0.5}
        client._set_cached("ticker_m1", ticker)
        result = await client.get_market_ticker("m1")
        assert result == ticker

    @pytest.mark.asyncio
    async def test_should_fetch_ticker(self):
        client = PolymarketClient(session=MagicMock())
        data = {"price": 0.55}
        client._make_request = AsyncMock(return_value=data)
        result = await client.get_market_ticker("m1")
        assert result["price"] == 0.55

    @pytest.mark.asyncio
    async def test_should_return_none_on_error(self):
        client = PolymarketClient(session=MagicMock())
        client._make_request = AsyncMock(side_effect=PolymarketAPIError("err"))
        result = await client.get_market_ticker("m1")
        assert result is None


class TestParseMarketData:
    def test_should_parse_full_market_data(self):
        client = PolymarketClient()
        data = {
            "id": "m1",
            "slug": "test",
            "question": "Q?",
            "description": "Desc",
            "category": "politics",
            "status": "open",
            "createdTime": "2026-01-01T00:00:00Z",
            "endTime": "2026-06-01T00:00:00Z",
            "outcomes": [
                {
                    "name": "Yes",
                    "ticker": "YES",
                    "price": 0.6,
                    "volume24h": 1000,
                    "lastUpdated": "2026-01-15T00:00:00Z",
                },
                {"name": "No", "ticker": "NO", "price": 0.4, "volume24h": 500},
            ],
            "volume24h": 1500,
            "liquidity": 5000,
            "openInterest": 2000,
        }
        market = client._parse_market_data(data)
        assert market.id == "m1"
        assert market.implied_probability == 0.6
        assert market.end_time is not None

    def test_should_handle_unknown_category(self):
        client = PolymarketClient()
        data = {
            "id": "m1",
            "category": "unknown_cat",
            "status": "open",
            "createdTime": "2026-01-01T00:00:00Z",
            "outcomes": [],
        }
        market = client._parse_market_data(data)
        from overblick.plugins.polymarket_monitor.models import MarketCategory

        assert market.category == MarketCategory.OTHER

    def test_should_handle_unknown_status(self):
        client = PolymarketClient()
        data = {
            "id": "m1",
            "category": "politics",
            "status": "unknown_status",
            "createdTime": "2026-01-01T00:00:00Z",
            "outcomes": [],
        }
        market = client._parse_market_data(data)
        from overblick.plugins.polymarket_monitor.models import MarketStatus

        assert market.status == MarketStatus.OPEN

    def test_should_handle_no_end_time(self):
        client = PolymarketClient()
        data = {
            "id": "m1",
            "category": "other",
            "status": "open",
            "createdTime": "2026-01-01T00:00:00Z",
            "outcomes": [],
        }
        market = client._parse_market_data(data)
        assert market.end_time is None

    def test_should_handle_outcome_without_last_updated(self):
        client = PolymarketClient()
        data = {
            "id": "m1",
            "category": "other",
            "status": "open",
            "createdTime": "2026-01-01T00:00:00Z",
            "outcomes": [
                {"name": "Yes", "ticker": "YES", "price": 0.5},
            ],
        }
        market = client._parse_market_data(data)
        assert len(market.outcomes) == 1

    def test_should_handle_non_binary_market_no_implied_prob(self):
        client = PolymarketClient()
        data = {
            "id": "m1",
            "category": "other",
            "status": "open",
            "createdTime": "2026-01-01T00:00:00Z",
            "outcomes": [
                {"name": "A", "ticker": "A", "price": 0.33},
                {"name": "B", "ticker": "B", "price": 0.33},
                {"name": "C", "ticker": "C", "price": 0.33},
            ],
        }
        market = client._parse_market_data(data)
        assert market.implied_probability is None

    def test_should_handle_binary_without_yes_outcome(self):
        client = PolymarketClient()
        data = {
            "id": "m1",
            "category": "other",
            "status": "open",
            "createdTime": "2026-01-01T00:00:00Z",
            "outcomes": [
                {"name": "A", "ticker": "A", "price": 0.5},
                {"name": "B", "ticker": "B", "price": 0.5},
            ],
        }
        market = client._parse_market_data(data)
        assert market.implied_probability is None
