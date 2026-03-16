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


class TestRateLimiterDefaults:
    """Kill mutants in default parameter values."""

    def test_should_default_max_tokens_to_10(self):
        rl = RateLimiter()
        assert rl._max_tokens == 10

    def test_should_default_refill_rate_to_1(self):
        rl = RateLimiter()
        assert rl._refill_rate == 1.0

    def test_should_default_max_buckets_to_10000(self):
        rl = RateLimiter()
        assert rl._max_buckets == 10_000

    def test_should_use_custom_max_tokens(self):
        rl = RateLimiter(max_tokens=5)
        assert rl._max_tokens == 5

    def test_should_use_custom_refill_rate(self):
        rl = RateLimiter(refill_rate=2.0)
        assert rl._refill_rate == 2.0

    def test_should_use_custom_max_buckets(self):
        rl = RateLimiter(max_buckets=50)
        assert rl._max_buckets == 50


class TestRateLimiterCost:
    """Kill mutants in cost parameter handling."""

    def test_should_use_default_cost_of_1(self):
        """Default cost is 1.0."""
        rl = RateLimiter(max_tokens=2, refill_rate=0.001)
        assert rl.allow()  # cost 1
        assert rl.allow()  # cost 1
        assert not rl.allow()  # depleted

    def test_should_consume_custom_cost(self):
        """Custom cost consumes more tokens."""
        rl = RateLimiter(max_tokens=5, refill_rate=0.001)
        assert rl.allow(cost=3.0)  # 5-3=2 remaining
        assert not rl.allow(cost=3.0)  # need 3, only 2 left

    def test_should_allow_exact_remaining_tokens(self):
        """Cost equal to remaining tokens is allowed."""
        rl = RateLimiter(max_tokens=3, refill_rate=0.001)
        assert rl.allow(cost=3.0)
        assert not rl.allow(cost=0.001)  # essentially 0 tokens left

    def test_should_use_default_key_default(self):
        """Default key is 'default'."""
        rl = RateLimiter(max_tokens=1, refill_rate=0.001)
        rl.allow()  # depletes "default"
        assert not rl.allow()  # "default" is depleted
        assert rl.allow("other")  # "other" is fresh

    def test_retry_after_with_custom_cost(self):
        """retry_after respects custom cost."""
        rl = RateLimiter(max_tokens=1, refill_rate=1.0)
        rl.allow()  # depleted
        wait = rl.retry_after(cost=2.0)
        assert wait > 1.0  # needs 2 tokens at 1/s


class TestRateLimiterRefill:
    """Kill mutants in _refill logic."""

    def test_should_cap_tokens_at_max(self):
        """Tokens never exceed max_tokens."""
        rl = RateLimiter(max_tokens=5, refill_rate=100.0)
        rl.allow()  # consume 1
        import time
        time.sleep(0.01)  # Let some time pass for refill
        rl._refill(rl._buckets["default"])
        assert rl._buckets["default"].tokens <= 5.0

    def test_should_refill_based_on_elapsed_time(self):
        """Tokens added = elapsed * refill_rate."""
        rl = RateLimiter(max_tokens=10, refill_rate=100.0)
        # Deplete
        for _ in range(10):
            rl.allow()
        assert not rl.allow()

        import time
        time.sleep(0.05)  # At 100 t/s, should get ~5 tokens
        assert rl.allow()  # Should have refilled

    def test_should_update_last_refill_time(self):
        """last_refill is updated on each refill."""
        import time
        rl = RateLimiter(max_tokens=5, refill_rate=1.0)
        rl.allow()
        bucket = rl._buckets["default"]
        old_refill = bucket.last_refill
        time.sleep(0.01)
        rl._refill(bucket)
        assert bucket.last_refill > old_refill


class TestGetBucket:
    """Kill mutants in _get_bucket."""

    def test_should_create_new_bucket_with_max_tokens(self):
        """New buckets start with max_tokens."""
        rl = RateLimiter(max_tokens=7, refill_rate=1.0)
        bucket = rl._get_bucket("new_key")
        assert bucket.tokens == 7.0
        assert bucket.max_tokens == 7.0
        assert bucket.refill_rate == 1.0

    def test_should_move_accessed_bucket_to_end(self):
        """Accessing an existing bucket moves it to end (LRU)."""
        rl = RateLimiter(max_tokens=5, refill_rate=1.0, max_buckets=3)
        rl._get_bucket("a")
        rl._get_bucket("b")
        rl._get_bucket("a")  # Move "a" to end
        # "b" should now be at the front (LRU)
        assert list(rl._buckets.keys())[0] == "b"

    def test_should_evict_lru_when_at_capacity(self):
        """Eviction happens when buckets >= max_buckets."""
        rl = RateLimiter(max_tokens=5, refill_rate=1.0, max_buckets=2)
        rl._get_bucket("a")
        rl._get_bucket("b")
        rl._get_bucket("c")  # Should evict "a"
        assert "a" not in rl._buckets
        assert "c" in rl._buckets
        assert len(rl._buckets) == 2
