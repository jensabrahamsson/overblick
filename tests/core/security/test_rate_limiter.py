"""Tests for rate limiter."""

from overblick.core.security.rate_limiter import RateLimiter


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

    def test_lru_eviction(self):
        """Buckets are evicted LRU when max_buckets is reached."""
        rl = RateLimiter(max_tokens=5, refill_rate=1.0, max_buckets=3)
        rl.allow("a")
        rl.allow("b")
        rl.allow("c")
        # "a" is LRU — adding "d" should evict it
        rl.allow("d")
        assert len(rl._buckets) == 3
        assert "a" not in rl._buckets
        assert "d" in rl._buckets

    def test_lru_access_refreshes(self):
        """Accessing a bucket moves it to end (not evicted next)."""
        rl = RateLimiter(max_tokens=5, refill_rate=1.0, max_buckets=3)
        rl.allow("a")
        rl.allow("b")
        rl.allow("c")
        # Access "a" again — now "b" is LRU
        rl.allow("a")
        rl.allow("d")
        assert "a" in rl._buckets
        assert "b" not in rl._buckets

    def test_max_buckets_never_exceeded(self):
        """Memory stays bounded regardless of unique keys."""
        rl = RateLimiter(max_tokens=1, refill_rate=0.01, max_buckets=100)
        for i in range(500):
            rl.allow(f"key_{i}")
        assert len(rl._buckets) <= 100

    def test_retry_after_with_depleted_bucket(self):
        """retry_after() returns a positive value when bucket is depleted."""
        rl = RateLimiter(max_tokens=2, refill_rate=0.5)  # 1 token / 2s
        rl.allow()  # consume 1
        rl.allow()  # consume 2 — now depleted
        wait = rl.retry_after()
        assert wait > 0
        # Should refill in ~2 seconds (at 0.5 t/s rate to get 1 token)
        assert wait <= 2.5

    def test_retry_after_available_tokens(self):
        """retry_after() returns 0.0 when tokens are available."""
        rl = RateLimiter(max_tokens=5, refill_rate=1.0)
        assert rl.retry_after() == 0.0

    # NOTE: No concurrent access test is needed here.
    # asyncio is cooperative (single-threaded event loop with explicit yield points).
    # There is no `await` between the token check and the token deduction in `allow()`,
    # so data races are impossible in asyncio. A concurrent test would be misleading.
