"""
Tests for MoltbookRequestProxy — rate limiting, caching, and metrics.
"""

import asyncio
import time
from unittest.mock import patch

import pytest

from overblick.plugins.moltbook.request_proxy import (
    RequestCache,
    MoltbookRequestProxy,
)


class TestRequestCache:
    """Tests for the TTL-based request cache."""

    def test_set_and_get(self):
        cache = RequestCache(ttl_seconds=60)
        cache.set("GET", "/api/feed", {"items": [1, 2, 3]})
        result = cache.get("GET", "/api/feed")
        assert result == {"items": [1, 2, 3]}

    def test_cache_miss(self):
        cache = RequestCache(ttl_seconds=60)
        assert cache.get("GET", "/api/unknown") is None

    def test_expired_entry_returns_none(self):
        cache = RequestCache(ttl_seconds=1)
        cache.set("GET", "/api/feed", {"data": "old"})
        # Manually expire the entry
        key = cache._make_key("GET", "/api/feed")
        cache._cache[key] = ({"data": "old"}, time.time() - 1)
        assert cache.get("GET", "/api/feed") is None

    def test_different_endpoints_different_keys(self):
        cache = RequestCache(ttl_seconds=60)
        cache.set("GET", "/api/feed", {"feed": True})
        cache.set("GET", "/api/profile", {"profile": True})
        assert cache.get("GET", "/api/feed") == {"feed": True}
        assert cache.get("GET", "/api/profile") == {"profile": True}

    def test_params_affect_cache_key(self):
        cache = RequestCache(ttl_seconds=60)
        cache.set("GET", "/api/feed", {"page1": True}, params={"page": 1})
        cache.set("GET", "/api/feed", {"page2": True}, params={"page": 2})
        assert cache.get("GET", "/api/feed", params={"page": 1}) == {"page1": True}
        assert cache.get("GET", "/api/feed", params={"page": 2}) == {"page2": True}

    def test_clear(self):
        cache = RequestCache(ttl_seconds=60)
        cache.set("GET", "/a", {"a": 1})
        cache.set("GET", "/b", {"b": 2})
        cache.clear()
        assert cache.get("GET", "/a") is None
        assert cache.get("GET", "/b") is None


class TestMoltbookRequestProxy:
    """Tests for the request proxy."""

    def test_initial_stats(self):
        proxy = MoltbookRequestProxy(max_requests_per_minute=10)
        stats = proxy.get_stats()
        assert stats["total_requests"] == 0
        assert stats["cached_requests"] == 0
        assert stats["rate_limited_count"] == 0

    @pytest.mark.asyncio
    async def test_request_returns_proxy_marker(self):
        proxy = MoltbookRequestProxy(max_requests_per_minute=100)
        result = await proxy.request("GET", "/api/feed")
        assert result["_proxy_marker"] is True
        assert result["method"] == "GET"
        assert result["endpoint"] == "/api/feed"

    @pytest.mark.asyncio
    async def test_get_request_cached(self):
        proxy = MoltbookRequestProxy(
            max_requests_per_minute=100, enable_cache=True
        )
        # First request
        await proxy.request("GET", "/api/feed")
        # Cache the response
        proxy.cache_response("GET", "/api/feed", {"cached": True})
        # Second request should hit cache
        result = await proxy.request("GET", "/api/feed")
        assert result == {"cached": True}
        assert proxy.get_stats()["cached_requests"] == 1

    @pytest.mark.asyncio
    async def test_post_not_cached(self):
        proxy = MoltbookRequestProxy(
            max_requests_per_minute=100, enable_cache=True
        )
        proxy.cache_response("POST", "/api/post", {"should_not_cache": True})
        result = await proxy.request("POST", "/api/post")
        assert result["_proxy_marker"] is True

    @pytest.mark.asyncio
    async def test_skip_cache_flag(self):
        proxy = MoltbookRequestProxy(
            max_requests_per_minute=100, enable_cache=True
        )
        proxy.cache_response("GET", "/api/feed", {"cached": True})
        result = await proxy.request("GET", "/api/feed", skip_cache=True)
        assert result["_proxy_marker"] is True

    @pytest.mark.asyncio
    async def test_cache_disabled(self):
        proxy = MoltbookRequestProxy(
            max_requests_per_minute=100, enable_cache=False
        )
        proxy.cache_response("GET", "/api/feed", {"cached": True})
        result = await proxy.request("GET", "/api/feed")
        assert result["_proxy_marker"] is True

    def test_handle_rate_limit_response(self):
        proxy = MoltbookRequestProxy()
        proxy.handle_rate_limit_response(60)
        assert proxy._rate_limit_until is not None
        assert proxy.get_stats()["rate_limited_count"] == 1

    def test_clear_cache(self):
        proxy = MoltbookRequestProxy(enable_cache=True)
        proxy.cache_response("GET", "/api/feed", {"data": True})
        proxy.clear_cache()
        assert proxy._cache.get("GET", "/api/feed") is None

    @pytest.mark.asyncio
    async def test_request_increments_total(self):
        proxy = MoltbookRequestProxy(max_requests_per_minute=100)
        await proxy.request("GET", "/api/feed")
        await proxy.request("POST", "/api/post")
        assert proxy.get_stats()["total_requests"] == 2

    @pytest.mark.asyncio
    async def test_check_rate_limit_under_limit(self):
        proxy = MoltbookRequestProxy(max_requests_per_minute=100)
        result = await proxy.check_rate_limit()
        assert result is True

    def test_stats_cache_hit_rate(self):
        proxy = MoltbookRequestProxy()
        proxy._total_requests = 10
        proxy._cached_requests = 3
        stats = proxy.get_stats()
        assert stats["cache_hit_rate"] == "30.0%"
