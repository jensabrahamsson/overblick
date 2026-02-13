"""
Rate limiter for Moltbook API.

Implements token bucket rate limiting with specific limits for different actions.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TokenBucket(BaseModel):
    """
    Token bucket rate limiter.

    Tokens replenish at a constant rate up to a maximum capacity.
    Each action consumes one token.
    """
    capacity: int
    refill_rate: float  # tokens per second
    tokens: Optional[float] = None
    last_refill: Optional[float] = None

    def model_post_init(self, __context) -> None:
        if self.tokens is None:
            self.tokens = float(self.capacity)
        if self.last_refill is None:
            self.last_refill = time.monotonic()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    def try_acquire(self) -> bool:
        """
        Try to acquire a token.

        Returns:
            True if token acquired, False if rate limited.
        """
        self._refill()
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False

    async def acquire(self, timeout: float = 60.0) -> bool:
        """
        Acquire a token, waiting if necessary.

        Args:
            timeout: Maximum time to wait for a token.

        Returns:
            True if token acquired, False if timeout.
        """
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            if self.try_acquire():
                return True
            # Calculate wait time until next token
            self._refill()
            wait_time = (1 - self.tokens) / self.refill_rate
            wait_time = min(wait_time, timeout - (time.monotonic() - start))
            if wait_time > 0:
                await asyncio.sleep(wait_time)
        return False

    def time_until_available(self) -> float:
        """
        Calculate time until a token is available.

        Returns:
            Seconds until next token, or 0 if available now.
        """
        self._refill()
        if self.tokens >= 1:
            return 0
        return (1 - self.tokens) / self.refill_rate


class MoltbookRateLimiter:
    """
    Rate limiter for Moltbook API with different limits per action type.

    Rate limits from Moltbook:
    - 100 requests/minute (general)
    - 1 post per 30 minutes
    - 1 comment per 20 seconds
    - 50 comments per day
    """

    def __init__(
        self,
        requests_per_minute: int = 100,
        post_interval_minutes: int = 30,
        comment_interval_seconds: int = 20,
        max_comments_per_day: int = 50,
    ):
        """
        Initialize rate limiter with Moltbook limits.

        Args:
            requests_per_minute: General API request limit
            post_interval_minutes: Minimum time between posts
            comment_interval_seconds: Minimum time between comments
            max_comments_per_day: Maximum comments per day
        """
        # General request bucket
        self._general = TokenBucket(
            capacity=requests_per_minute,
            refill_rate=requests_per_minute / 60.0,
        )

        # Post bucket (1 token per interval)
        self._posts = TokenBucket(
            capacity=1,
            refill_rate=1 / (post_interval_minutes * 60),
        )

        # Comment bucket (short interval)
        self._comments = TokenBucket(
            capacity=1,
            refill_rate=1 / comment_interval_seconds,
        )

        # Daily comment tracking
        self._max_daily_comments = max_comments_per_day
        self._daily_comments = 0
        self._day_start = self._get_day_start()

        logger.info(
            f"MoltbookRateLimiter initialized: "
            f"{requests_per_minute}/min, "
            f"post every {post_interval_minutes}min, "
            f"comment every {comment_interval_seconds}s, "
            f"{max_comments_per_day} comments/day"
        )

    def _get_day_start(self) -> float:
        """Get timestamp of current day start (midnight UTC)."""
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.timestamp()

    def _check_day_reset(self) -> None:
        """Reset daily counters if new day."""
        current_day = self._get_day_start()
        if current_day > self._day_start:
            logger.info(f"New day - resetting daily comment count (was {self._daily_comments})")
            self._daily_comments = 0
            self._day_start = current_day

    async def acquire_request(self) -> bool:
        """
        Acquire permission for a general API request.

        Returns:
            True if allowed, False if rate limited.
        """
        return await self._general.acquire()

    async def acquire_post(self) -> bool:
        """
        Acquire permission to create a post.

        Returns:
            True if allowed, False if rate limited.
        """
        # Must pass both general and post-specific limits
        if not await self._general.acquire():
            return False
        return await self._posts.acquire()

    async def acquire_comment(self) -> bool:
        """
        Acquire permission to create a comment.

        Returns:
            True if allowed, False if rate limited.
        """
        self._check_day_reset()

        # Check daily limit
        if self._daily_comments >= self._max_daily_comments:
            logger.warning(f"Daily comment limit reached ({self._max_daily_comments})")
            return False

        # Must pass both general and comment-specific limits
        if not await self._general.acquire():
            return False

        if not await self._comments.acquire():
            return False

        self._daily_comments += 1
        return True

    def can_post(self) -> bool:
        """Check if posting is allowed without consuming a token."""
        return self._general.try_acquire() and self._posts.tokens >= 1

    def can_comment(self) -> bool:
        """Check if commenting is allowed without consuming a token."""
        self._check_day_reset()
        return (
            self._daily_comments < self._max_daily_comments
            and self._comments.tokens >= 1
        )

    def time_until_post(self) -> float:
        """Get seconds until posting is allowed."""
        return max(
            self._general.time_until_available(),
            self._posts.time_until_available(),
        )

    def time_until_comment(self) -> float:
        """Get seconds until commenting is allowed."""
        self._check_day_reset()
        if self._daily_comments >= self._max_daily_comments:
            # Return time until midnight
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc)
            midnight = (now + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            return (midnight - now).total_seconds()

        return max(
            self._general.time_until_available(),
            self._comments.time_until_available(),
        )

    def get_status(self) -> Dict[str, Any]:
        """Get current rate limit status."""
        self._check_day_reset()
        return {
            "general_tokens": self._general.tokens,
            "post_available": self._posts.tokens >= 1,
            "post_wait_seconds": self._posts.time_until_available(),
            "comment_available": self._comments.tokens >= 1,
            "comment_wait_seconds": self._comments.time_until_available(),
            "daily_comments_used": self._daily_comments,
            "daily_comments_remaining": self._max_daily_comments - self._daily_comments,
        }
