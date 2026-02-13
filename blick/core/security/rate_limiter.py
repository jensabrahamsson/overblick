"""
Generic token bucket rate limiter.

Provides per-key rate limiting with configurable burst and refill rates.
Used at framework level (LLM calls) and by plugins (API calls).
"""

import logging
import time

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class _Bucket(BaseModel):
    """Single rate limit bucket."""
    tokens: float
    last_refill: float
    max_tokens: float
    refill_rate: float  # tokens per second


class RateLimiter:
    """
    Token bucket rate limiter.

    Usage:
        limiter = RateLimiter(max_tokens=10, refill_rate=1.0)
        if limiter.allow("api_calls"):
            make_api_call()
        else:
            wait_seconds = limiter.retry_after("api_calls")
    """

    def __init__(
        self,
        max_tokens: float = 10,
        refill_rate: float = 1.0,
    ):
        """
        Args:
            max_tokens: Maximum burst capacity
            refill_rate: Tokens added per second
        """
        self._max_tokens = max_tokens
        self._refill_rate = refill_rate
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str = "default", cost: float = 1.0) -> bool:
        """
        Check if an action is allowed and consume tokens.

        Args:
            key: Rate limit key (e.g. "api_calls", "llm_requests")
            cost: Token cost of this action

        Returns:
            True if allowed (tokens consumed), False if rate limited
        """
        bucket = self._get_bucket(key)
        self._refill(bucket)

        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return True

        return False

    def retry_after(self, key: str = "default", cost: float = 1.0) -> float:
        """
        Get seconds until enough tokens are available.

        Args:
            key: Rate limit key
            cost: Required tokens

        Returns:
            Seconds to wait (0.0 if already allowed)
        """
        bucket = self._get_bucket(key)
        self._refill(bucket)

        if bucket.tokens >= cost:
            return 0.0

        needed = cost - bucket.tokens
        return needed / bucket.refill_rate

    def _get_bucket(self, key: str) -> _Bucket:
        """Get or create bucket for key."""
        if key not in self._buckets:
            self._buckets[key] = _Bucket(
                tokens=self._max_tokens,
                last_refill=time.monotonic(),
                max_tokens=self._max_tokens,
                refill_rate=self._refill_rate,
            )
        return self._buckets[key]

    def _refill(self, bucket: _Bucket) -> None:
        """Refill bucket based on elapsed time."""
        now = time.monotonic()
        elapsed = now - bucket.last_refill
        bucket.tokens = min(
            bucket.max_tokens,
            bucket.tokens + elapsed * bucket.refill_rate,
        )
        bucket.last_refill = now
