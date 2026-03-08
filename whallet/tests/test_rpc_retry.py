"""
Unit tests for RPC retry and circuit breaker logic.

Tests cover:
1. Retry policy calculations
2. Error categorization
3. Circuit breaker state transitions
4. with_retry function behavior
5. Multi-provider failover
6. Edge cases and error handling

SECURITY: Validates fail-closed patterns and proper error handling.
"""
import os
import pytest
import asyncio
import time
from unittest.mock import AsyncMock

# Set test environment
os.environ["PYTEST_RUNNING"] = "1"
os.environ["WHALLET_SIMULATION_ENABLED"] = "true"

from whallet.rpc_retry import (
    RPCErrorCategory,
    RPCError,
    RateLimitError,
    TimeoutError,
    CircuitOpenError,
    NonRetryableError,
    RetryPolicy,
    RETRY_POLICIES,
    CircuitBreaker,
    circuit_breaker,
    categorize_error,
    with_retry,
    retry_decorator,
    MultiProviderRPC,
    get_circuit_breaker,
    get_retry_policy,
)


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset global circuit breaker before and after each test."""
    circuit_breaker.reset_all()
    yield
    circuit_breaker.reset_all()


class TestRPCErrorCategory:
    """Test error category enum."""

    def test_all_categories_exist(self):
        """Test that all expected categories exist."""
        assert RPCErrorCategory.RETRYABLE
        assert RPCErrorCategory.RATE_LIMITED
        assert RPCErrorCategory.NON_RETRYABLE
        assert RPCErrorCategory.TIMEOUT
        assert RPCErrorCategory.CIRCUIT_OPEN


class TestRPCError:
    """Test RPC error classes."""

    def test_rpc_error_is_retryable(self):
        """Test that default RPC error is retryable."""
        error = RPCError("Test error")
        assert error.is_retryable()

    def test_rate_limit_error_is_retryable(self):
        """Test that rate limit errors are retryable."""
        error = RateLimitError("Rate limited", retry_after=60)
        assert error.is_retryable()
        assert error.retry_after == 60
        assert error.status_code == 429

    def test_timeout_error_is_retryable(self):
        """Test that timeout errors are retryable."""
        error = TimeoutError("Connection timeout")
        assert error.is_retryable()
        assert error.category == RPCErrorCategory.TIMEOUT

    def test_circuit_open_error_not_retryable(self):
        """Test that circuit open errors are not retryable."""
        error = CircuitOpenError("Circuit open", provider="infura")
        assert not error.is_retryable()
        assert error.category == RPCErrorCategory.CIRCUIT_OPEN

    def test_non_retryable_error(self):
        """Test non-retryable error."""
        error = NonRetryableError("Invalid params", status_code=400)
        assert not error.is_retryable()
        assert error.status_code == 400

    def test_error_preserves_original(self):
        """Test that original error is preserved."""
        original = ValueError("Original error")
        error = RPCError("Wrapped", original_error=original)
        assert error.original_error is original


class TestRetryPolicy:
    """Test retry policy calculations."""

    def test_default_policy_values(self):
        """Test default policy has expected values."""
        policy = RetryPolicy()
        assert policy.max_retries == 5
        assert policy.base_delay == 0.5
        assert policy.max_delay == 30.0
        assert policy.exponential_base == 2.0

    def test_exponential_backoff(self):
        """Test that delay increases exponentially."""
        policy = RetryPolicy(base_delay=1.0, jitter_factor=0.0)

        delay_0 = policy.calculate_delay(0)
        delay_1 = policy.calculate_delay(1)
        delay_2 = policy.calculate_delay(2)

        assert delay_0 == 1.0  # 1 * 2^0 = 1
        assert delay_1 == 2.0  # 1 * 2^1 = 2
        assert delay_2 == 4.0  # 1 * 2^2 = 4

    def test_max_delay_cap(self):
        """Test that delay is capped at max_delay."""
        policy = RetryPolicy(
            base_delay=1.0, max_delay=10.0, jitter_factor=0.0
        )

        # 1 * 2^5 = 32, but should be capped at 10
        delay = policy.calculate_delay(5)
        assert delay == 10.0

    def test_rate_limit_multiplier(self):
        """Test that rate limit multiplier is applied."""
        policy = RetryPolicy(
            base_delay=1.0, jitter_factor=0.0, rate_limit_multiplier=3.0
        )

        delay_normal = policy.calculate_delay(0, is_rate_limited=False)
        delay_rate_limited = policy.calculate_delay(0, is_rate_limited=True)

        assert delay_rate_limited == delay_normal * 3.0

    def test_jitter_adds_randomness(self):
        """Test that jitter adds randomness to delay."""
        policy = RetryPolicy(base_delay=1.0, jitter_factor=0.5)

        delays = [policy.calculate_delay(0) for _ in range(10)]

        # With jitter, delays should vary
        assert len(set(delays)) > 1  # Not all the same
        # All should be >= base_delay
        assert all(d >= 1.0 for d in delays)


class TestPredefinedPolicies:
    """Test predefined retry policies."""

    def test_default_policy_exists(self):
        """Test that default policy exists."""
        policy = get_retry_policy("default")
        assert policy is not None
        assert policy.max_retries == 5

    def test_transaction_policy_has_fewer_retries(self):
        """Test that transaction policy has fewer retries."""
        transaction = get_retry_policy("transaction")
        default = get_retry_policy("default")
        assert transaction.max_retries < default.max_retries

    def test_unknown_policy_returns_default(self):
        """Test that unknown policy name returns default."""
        policy = get_retry_policy("unknown_policy")
        assert policy == RETRY_POLICIES["default"]


class TestCategorizeError:
    """Test error categorization."""

    def test_429_is_rate_limited(self):
        """Test that 429 status is rate limited."""
        category = categorize_error(Exception("error"), status_code=429)
        assert category == RPCErrorCategory.RATE_LIMITED

    def test_400_is_non_retryable(self):
        """Test that 400 status is non-retryable."""
        category = categorize_error(Exception("error"), status_code=400)
        assert category == RPCErrorCategory.NON_RETRYABLE

    def test_401_is_non_retryable(self):
        """Test that 401 status is non-retryable."""
        category = categorize_error(Exception("error"), status_code=401)
        assert category == RPCErrorCategory.NON_RETRYABLE

    def test_500_is_retryable(self):
        """Test that 500 status is retryable."""
        category = categorize_error(Exception("error"), status_code=500)
        assert category == RPCErrorCategory.RETRYABLE

    def test_timeout_in_message(self):
        """Test that timeout in message is categorized as timeout."""
        category = categorize_error(Exception("connection timed out"))
        assert category == RPCErrorCategory.TIMEOUT

    def test_connection_error_is_retryable(self):
        """Test that connection errors are retryable."""
        category = categorize_error(Exception("connection refused"))
        assert category == RPCErrorCategory.RETRYABLE

    def test_invalid_params_is_non_retryable(self):
        """Test that invalid params is non-retryable."""
        category = categorize_error(Exception("invalid params in request"))
        assert category == RPCErrorCategory.NON_RETRYABLE

    def test_execution_reverted_is_non_retryable(self):
        """Test that execution reverted is non-retryable."""
        category = categorize_error(Exception("execution reverted"))
        assert category == RPCErrorCategory.NON_RETRYABLE

    def test_nonce_too_low_is_non_retryable(self):
        """Test that nonce too low is non-retryable."""
        category = categorize_error(Exception("nonce too low"))
        assert category == RPCErrorCategory.NON_RETRYABLE

    def test_unknown_error_is_retryable(self):
        """Test that unknown errors default to retryable."""
        category = categorize_error(Exception("some random error"))
        assert category == RPCErrorCategory.RETRYABLE


class TestCircuitBreaker:
    """Test circuit breaker state machine."""

    def test_initial_state_is_closed(self):
        """Test that initial state is closed."""
        cb = CircuitBreaker()
        assert not cb.is_open("test_provider")

    def test_open_after_threshold_failures(self):
        """Test that circuit opens after threshold failures."""
        cb = CircuitBreaker(failure_threshold=3)

        cb.record_failure("provider")
        assert not cb.is_open("provider")

        cb.record_failure("provider")
        assert not cb.is_open("provider")

        cb.record_failure("provider")
        assert cb.is_open("provider")

    def test_success_resets_failure_count(self):
        """Test that success resets failure count."""
        cb = CircuitBreaker(failure_threshold=3)

        cb.record_failure("provider")
        cb.record_failure("provider")
        cb.record_success("provider")

        # Failure count reset, should need 3 more failures
        cb.record_failure("provider")
        cb.record_failure("provider")
        assert not cb.is_open("provider")

    def test_half_open_after_timeout(self):
        """Test transition to half-open after timeout."""
        cb = CircuitBreaker(failure_threshold=2, timeout_seconds=0.1)

        # Open the circuit
        cb.record_failure("provider")
        cb.record_failure("provider")
        assert cb.is_open("provider")

        # Wait for timeout
        time.sleep(0.15)

        # Should be half-open now
        assert not cb.is_open("provider")
        status = cb.get_status("provider")
        assert status["state"] == "half_open"

    def test_close_after_success_in_half_open(self):
        """Test circuit closes after successes in half-open."""
        cb = CircuitBreaker(
            failure_threshold=2, success_threshold=2, timeout_seconds=0.05
        )

        # Open the circuit
        cb.record_failure("provider")
        cb.record_failure("provider")

        # Wait for timeout (transition to half-open)
        time.sleep(0.1)
        cb.is_open("provider")  # Triggers state check

        # Success in half-open
        cb.record_success("provider")
        cb.record_success("provider")

        status = cb.get_status("provider")
        assert status["state"] == "closed"

    def test_reopen_on_failure_in_half_open(self):
        """Test circuit reopens on failure in half-open."""
        cb = CircuitBreaker(failure_threshold=2, timeout_seconds=0.05)

        # Open the circuit
        cb.record_failure("provider")
        cb.record_failure("provider")

        # Wait for timeout
        time.sleep(0.1)
        cb.is_open("provider")  # Triggers transition to half-open

        # Failure in half-open should reopen
        cb.record_failure("provider")

        status = cb.get_status("provider")
        assert status["state"] == "open"

    def test_independent_providers(self):
        """Test that providers have independent state."""
        cb = CircuitBreaker(failure_threshold=2)

        # Fail provider1
        cb.record_failure("provider1")
        cb.record_failure("provider1")
        assert cb.is_open("provider1")

        # provider2 should still be closed
        assert not cb.is_open("provider2")

    def test_reset_clears_state(self):
        """Test that reset clears provider state."""
        cb = CircuitBreaker(failure_threshold=2)

        cb.record_failure("provider")
        cb.record_failure("provider")
        assert cb.is_open("provider")

        cb.reset("provider")
        assert not cb.is_open("provider")

    def test_reset_all_clears_all_providers(self):
        """Test that reset_all clears all states."""
        cb = CircuitBreaker(failure_threshold=1)

        cb.record_failure("provider1")
        cb.record_failure("provider2")

        cb.reset_all()

        assert not cb.is_open("provider1")
        assert not cb.is_open("provider2")


class TestWithRetry:
    """Test with_retry function."""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        """Test successful call returns immediately."""
        mock_func = AsyncMock(return_value="success")

        result = await with_retry(
            mock_func, policy_name="default", provider="test"
        )

        assert result == "success"
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_retryable_error(self):
        """Test that retryable errors trigger retries."""
        mock_func = AsyncMock(
            side_effect=[Exception("temporary"), "success"]
        )

        policy = RetryPolicy(max_retries=3, base_delay=0.01)
        result = await with_retry(
            mock_func, policy=policy, provider="test"
        )

        assert result == "success"
        assert mock_func.call_count == 2

    @pytest.mark.asyncio
    async def test_fails_after_max_retries(self):
        """Test that failure after max retries raises error."""
        mock_func = AsyncMock(side_effect=Exception("always fails"))

        policy = RetryPolicy(max_retries=2, base_delay=0.01)

        with pytest.raises(RPCError) as exc_info:
            await with_retry(
                mock_func, policy=policy, provider="test"
            )

        assert "failed after 3 attempts" in str(exc_info.value)
        assert mock_func.call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_error(self):
        """Test that non-retryable errors fail immediately."""
        # Simulate an error with status_code attribute
        error = Exception("invalid params")
        error.status_code = 400

        mock_func = AsyncMock(side_effect=error)

        policy = RetryPolicy(max_retries=3, base_delay=0.01)

        with pytest.raises(NonRetryableError):
            await with_retry(
                mock_func, policy=policy, provider="test"
            )

        # Should not retry
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_request(self):
        """Test that open circuit blocks request."""
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure("blocked_provider")

        mock_func = AsyncMock(return_value="success")

        with pytest.raises(CircuitOpenError):
            await with_retry(
                mock_func,
                provider="blocked_provider",
                circuit=cb,
            )

        # Function should not have been called
        assert mock_func.call_count == 0

    @pytest.mark.asyncio
    async def test_cancelled_error_not_retried(self):
        """Test that CancelledError is not retried."""
        mock_func = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await with_retry(
                mock_func,
                policy=RetryPolicy(max_retries=3, base_delay=0.01),
                provider="test",
            )

        assert mock_func.call_count == 1


class TestRetryDecorator:
    """Test retry decorator."""

    @pytest.mark.asyncio
    async def test_decorator_retries_on_failure(self):
        """Test that decorated function retries on failure."""
        call_count = 0

        @retry_decorator(policy_name="default", provider="test")
        async def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Exception("temporary error")
            return "success"

        # Reset circuit breaker for this test
        circuit_breaker.reset("test")

        result = await flaky_function()
        assert result == "success"
        assert call_count == 2


class TestMultiProviderRPC:
    """Test multi-provider RPC with failover."""

    @pytest.mark.asyncio
    async def test_uses_first_available_provider(self):
        """Test that first available provider is used."""
        providers = {
            "primary": "http://primary.rpc",
            "secondary": "http://secondary.rpc",
        }
        multi = MultiProviderRPC(providers)

        async def mock_call(url: str) -> str:
            return f"result_from_{url}"

        result = await multi.call(mock_call)
        assert result == "result_from_http://primary.rpc"

    @pytest.mark.asyncio
    async def test_failover_to_secondary(self):
        """Test failover when primary fails."""
        providers = {
            "primary": "http://primary.rpc",
            "secondary": "http://secondary.rpc",
        }
        cb = CircuitBreaker(failure_threshold=1)
        multi = MultiProviderRPC(providers, circuit=cb)

        call_urls = []

        async def mock_call(url: str) -> str:
            call_urls.append(url)
            if url == "http://primary.rpc":
                raise Exception("Primary failed")
            return "success_secondary"

        result = await multi.call(mock_call)
        assert result == "success_secondary"
        assert "http://primary.rpc" in call_urls
        assert "http://secondary.rpc" in call_urls

    @pytest.mark.asyncio
    async def test_all_providers_fail(self):
        """Test error when all providers fail."""
        providers = {
            "primary": "http://primary.rpc",
            "secondary": "http://secondary.rpc",
        }
        cb = CircuitBreaker(failure_threshold=2)  # Won't open immediately
        multi = MultiProviderRPC(providers, circuit=cb)

        async def mock_call(url: str) -> str:
            raise Exception("All fail")

        with pytest.raises(RPCError) as exc_info:
            await multi.call(mock_call)

        assert "All providers failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_skips_open_circuit_providers(self):
        """Test that providers with open circuits are skipped."""
        providers = {
            "blocked": "http://blocked.rpc",
            "available": "http://available.rpc",
        }
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure("blocked")  # Open circuit for blocked
        multi = MultiProviderRPC(providers, circuit=cb)

        async def mock_call(url: str) -> str:
            return f"from_{url}"

        result = await multi.call(mock_call)
        assert result == "from_http://available.rpc"

    @pytest.mark.asyncio
    async def test_no_available_providers(self):
        """Test error when no providers available."""
        providers = {"only": "http://only.rpc"}
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure("only")  # Open circuit
        multi = MultiProviderRPC(providers, circuit=cb)

        async def mock_call(url: str) -> str:
            return "success"

        with pytest.raises(RPCError) as exc_info:
            await multi.call(mock_call)

        assert "circuits open" in str(exc_info.value)

    def test_get_available_providers(self):
        """Test getting list of available providers."""
        providers = {
            "a": "http://a.rpc",
            "b": "http://b.rpc",
            "c": "http://c.rpc",
        }
        cb = CircuitBreaker(failure_threshold=1)
        multi = MultiProviderRPC(providers, circuit=cb)

        # All available initially
        assert set(multi.get_available_providers()) == {"a", "b", "c"}

        # Block one
        cb.record_failure("b")
        assert set(multi.get_available_providers()) == {"a", "c"}


class TestGlobalFunctions:
    """Test global utility functions."""

    def test_get_circuit_breaker_returns_global(self):
        """Test that get_circuit_breaker returns global instance."""
        cb = get_circuit_breaker()
        assert cb is circuit_breaker

    def test_get_retry_policy_returns_policy(self):
        """Test that get_retry_policy returns correct policy."""
        policy = get_retry_policy("transaction")
        assert policy == RETRY_POLICIES["transaction"]


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_zero_retries_fails_immediately(self):
        """Test that zero retries fails after first attempt."""
        mock_func = AsyncMock(side_effect=Exception("fails"))

        policy = RetryPolicy(max_retries=0, base_delay=0.01)

        with pytest.raises(RPCError):
            await with_retry(mock_func, policy=policy, provider="test")

        assert mock_func.call_count == 1

    def test_calculate_delay_with_zero_jitter(self):
        """Test delay calculation with zero jitter is deterministic."""
        policy = RetryPolicy(base_delay=1.0, jitter_factor=0.0)

        delays = [policy.calculate_delay(0) for _ in range(5)]
        assert all(d == delays[0] for d in delays)

    @pytest.mark.asyncio
    async def test_exception_with_status_code_attribute(self):
        """Test handling of exceptions with status_code attribute."""
        error = Exception("rate limited")
        error.status_code = 429

        mock_func = AsyncMock(side_effect=error)

        policy = RetryPolicy(max_retries=1, base_delay=0.01)

        with pytest.raises(RPCError):
            await with_retry(mock_func, policy=policy, provider="test")

        # Should retry once (rate limited is retryable)
        assert mock_func.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
