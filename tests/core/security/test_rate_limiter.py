"""Tests for rate limiter."""

from blick.core.security.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_allows_within_capacity(self):
        rl = RateLimiter(max_tokens=5, refill_rate=1.0)
        for _ in range(5):
            assert rl.allow()

    def test_blocks_when_empty(self):
        rl = RateLimiter(max_tokens=2, refill_rate=0.01)
        assert rl.allow()
        assert rl.allow()
        assert not rl.allow()

    def test_retry_after(self):
        rl = RateLimiter(max_tokens=1, refill_rate=1.0)
        rl.allow()  # Deplete
        wait = rl.retry_after()
        assert wait > 0
        assert wait <= 1.1

    def test_already_available(self):
        rl = RateLimiter(max_tokens=5, refill_rate=1.0)
        assert rl.retry_after() == 0
