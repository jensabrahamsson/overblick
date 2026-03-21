"""Tests for MoltbookRateLimiter and TokenBucket."""

import time
from unittest.mock import AsyncMock

import pytest

from overblick.plugins.moltbook.rate_limiter import MoltbookRateLimiter, TokenBucket


class TestTokenBucket:
    def test_initial_tokens_equals_capacity(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.tokens == 10.0
        assert bucket.last_refill is not None

    def test_initial_tokens_custom(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0, tokens=5.0)
        assert bucket.tokens == 5.0

    def test_last_refill_custom(self):
        t = time.monotonic() - 100
        bucket = TokenBucket(capacity=10, refill_rate=1.0, last_refill=t)
        assert bucket.last_refill == t

    def test_try_acquire_succeeds_when_tokens_available(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.try_acquire() is True
        assert bucket.tokens < 10.0

    def test_try_acquire_fails_when_no_tokens(self):
        bucket = TokenBucket(capacity=1, refill_rate=0.001, tokens=0.0)
        bucket.last_refill = time.monotonic()
        assert bucket.try_acquire() is False

    def test_refill_adds_tokens_over_time(self):
        bucket = TokenBucket(capacity=10, refill_rate=100.0, tokens=0.0)
        bucket.last_refill = time.monotonic() - 1.0
        bucket._refill()
        assert bucket.tokens == 10.0

    def test_refill_does_not_exceed_capacity(self):
        bucket = TokenBucket(capacity=5, refill_rate=1000.0, tokens=0.0)
        bucket.last_refill = time.monotonic() - 10.0
        bucket._refill()
        assert bucket.tokens == 5.0

    @pytest.mark.asyncio
    async def test_acquire_succeeds_immediately_when_available(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        result = await bucket.acquire(timeout=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_waits_and_succeeds(self):
        bucket = TokenBucket(capacity=1, refill_rate=100.0, tokens=0.0)
        bucket.last_refill = time.monotonic()
        result = await bucket.acquire(timeout=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_times_out(self):
        bucket = TokenBucket(capacity=1, refill_rate=0.001, tokens=0.0)
        bucket.last_refill = time.monotonic()
        result = await bucket.acquire(timeout=0.05)
        assert result is False

    def test_time_until_available_returns_zero_when_available(self):
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.time_until_available() == 0

    def test_time_until_available_returns_positive_when_empty(self):
        bucket = TokenBucket(capacity=1, refill_rate=1.0, tokens=0.0)
        bucket.last_refill = time.monotonic()
        wait = bucket.time_until_available()
        assert wait > 0


class TestMoltbookRateLimiter:
    def test_initialization(self):
        limiter = MoltbookRateLimiter(
            requests_per_minute=50,
            post_interval_minutes=15,
            comment_interval_seconds=10,
            max_comments_per_day=25,
        )
        assert limiter._max_daily_comments == 25
        assert limiter._daily_comments == 0

    @pytest.mark.asyncio
    async def test_acquire_request_succeeds(self):
        limiter = MoltbookRateLimiter()
        result = await limiter.acquire_request()
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_post_succeeds(self):
        limiter = MoltbookRateLimiter()
        result = await limiter.acquire_post()
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_post_fails_when_general_exhausted(self):
        limiter = MoltbookRateLimiter()
        mock_general = AsyncMock()
        mock_general.acquire = AsyncMock(return_value=False)
        limiter._general = mock_general
        result = await limiter.acquire_post()
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_post_fails_when_post_bucket_exhausted(self):
        limiter = MoltbookRateLimiter()
        mock_posts = AsyncMock()
        mock_posts.acquire = AsyncMock(return_value=False)
        limiter._posts = mock_posts
        result = await limiter.acquire_post()
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_comment_succeeds(self):
        limiter = MoltbookRateLimiter()
        result = await limiter.acquire_comment()
        assert result is True
        assert limiter._daily_comments == 1

    @pytest.mark.asyncio
    async def test_acquire_comment_fails_at_daily_limit(self):
        limiter = MoltbookRateLimiter(max_comments_per_day=0)
        result = await limiter.acquire_comment()
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_comment_fails_when_general_exhausted(self):
        limiter = MoltbookRateLimiter()
        mock_general = AsyncMock()
        mock_general.acquire = AsyncMock(return_value=False)
        limiter._general = mock_general
        result = await limiter.acquire_comment()
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_comment_fails_when_comment_bucket_exhausted(self):
        limiter = MoltbookRateLimiter()
        mock_comments = AsyncMock()
        mock_comments.acquire = AsyncMock(return_value=False)
        limiter._comments = mock_comments
        result = await limiter.acquire_comment()
        assert result is False

    def test_can_post_when_available(self):
        limiter = MoltbookRateLimiter()
        assert limiter.can_post() is True

    def test_can_post_when_general_unavailable(self):
        limiter = MoltbookRateLimiter()
        # Exhaust general
        limiter._general.tokens = 0.0
        limiter._general.last_refill = time.monotonic()
        limiter._general.refill_rate = 0.0001
        assert limiter.can_post() is False

    def test_can_post_when_post_bucket_empty(self):
        limiter = MoltbookRateLimiter()
        limiter._posts.tokens = 0.0
        assert limiter.can_post() is False

    def test_can_comment_when_available(self):
        limiter = MoltbookRateLimiter()
        assert limiter.can_comment() is True

    def test_can_comment_when_daily_limit_reached(self):
        limiter = MoltbookRateLimiter(max_comments_per_day=5)
        limiter._daily_comments = 5
        assert limiter.can_comment() is False

    def test_can_comment_when_bucket_empty(self):
        limiter = MoltbookRateLimiter()
        limiter._comments.tokens = 0.0
        assert limiter.can_comment() is False

    def test_time_until_post(self):
        limiter = MoltbookRateLimiter()
        assert limiter.time_until_post() == 0

    def test_time_until_post_returns_max_of_general_and_post(self):
        limiter = MoltbookRateLimiter()
        limiter._posts.tokens = 0.0
        limiter._posts.last_refill = time.monotonic()
        wait = limiter.time_until_post()
        assert wait > 0

    def test_time_until_comment(self):
        limiter = MoltbookRateLimiter()
        assert limiter.time_until_comment() == 0

    def test_time_until_comment_at_daily_limit(self):
        limiter = MoltbookRateLimiter(max_comments_per_day=0)
        wait = limiter.time_until_comment()
        assert wait > 0

    def test_time_until_comment_returns_max_of_general_and_comment(self):
        limiter = MoltbookRateLimiter()
        limiter._comments.tokens = 0.0
        limiter._comments.last_refill = time.monotonic()
        wait = limiter.time_until_comment()
        assert wait > 0

    def test_get_status(self):
        limiter = MoltbookRateLimiter()
        status = limiter.get_status()
        assert "general_tokens" in status
        assert "post_available" in status
        assert "comment_available" in status
        assert "daily_comments_used" in status
        assert "daily_comments_remaining" in status
        assert "post_wait_seconds" in status
        assert "comment_wait_seconds" in status

    def test_check_day_reset_resets_on_new_day(self):
        limiter = MoltbookRateLimiter()
        limiter._daily_comments = 10
        limiter._day_start = limiter._day_start - 86401
        limiter._check_day_reset()
        assert limiter._daily_comments == 0

    def test_check_day_reset_no_reset_same_day(self):
        limiter = MoltbookRateLimiter()
        limiter._daily_comments = 5
        limiter._check_day_reset()
        assert limiter._daily_comments == 5

    def test_get_day_start_returns_midnight_timestamp(self):
        limiter = MoltbookRateLimiter()
        day_start = limiter._get_day_start()
        assert isinstance(day_start, float)
        assert day_start > 0
