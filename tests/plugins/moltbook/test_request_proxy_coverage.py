"""
Additional coverage tests for request_proxy module.

Covers uncovered lines:
- 122-130: wait_for_rate_limit with active rate limit
- 148-157: check_rate_limit when at capacity (recursive wait)
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from overblick.plugins.moltbook.request_proxy import MoltbookRequestProxy


class TestWaitForRateLimit:
    @pytest.mark.asyncio
    async def test_should_wait_when_rate_limited(self):
        """Lines 122-130: waits and clears rate limit."""
        proxy = MoltbookRequestProxy()
        # Set rate limit to a time in the very near future
        proxy._rate_limit_until = datetime.now() + timedelta(milliseconds=50)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await proxy.wait_for_rate_limit()
            mock_sleep.assert_called_once()
            assert proxy._rate_limit_until is None

    @pytest.mark.asyncio
    async def test_should_skip_when_not_rate_limited(self):
        """No wait when not rate limited."""
        proxy = MoltbookRequestProxy()
        proxy._rate_limit_until = None

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await proxy.wait_for_rate_limit()
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_should_skip_when_rate_limit_expired(self):
        """No wait when rate limit is in the past."""
        proxy = MoltbookRequestProxy()
        proxy._rate_limit_until = datetime.now() - timedelta(seconds=10)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await proxy.wait_for_rate_limit()
            mock_sleep.assert_not_called()


class TestCheckRateLimitAtCapacity:
    @pytest.mark.asyncio
    async def test_should_wait_and_recheck_when_at_limit(self):
        """Lines 148-157: waits when request times are at capacity."""
        import time

        proxy = MoltbookRequestProxy(max_requests_per_minute=2)
        # Fill up the request times with recent timestamps
        now = time.time()
        proxy._request_times = [now - 59, now]  # 2 requests in last minute

        # After sleep, the old request will be expired and check passes
        async def fake_sleep(seconds):
            # Simulate passage of time by clearing old requests
            proxy._request_times = []

        with patch("asyncio.sleep", side_effect=fake_sleep):
            result = await proxy.check_rate_limit()
            assert result is True


class TestCacheDisabled:
    def test_should_not_cache_when_disabled(self):
        """Cache disabled means no caching."""
        proxy = MoltbookRequestProxy(enable_cache=False)
        proxy.cache_response("GET", "/api/feed", {"data": True})
        assert proxy._cache.get("GET", "/api/feed") is None
