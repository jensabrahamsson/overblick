"""
RPC retry and circuit breaker logic for Whallet.

Provides:
1. Exponential backoff with jitter
2. Circuit breaker pattern for repeated failures
3. Configurable retry policies per operation type
4. Error categorization (retryable vs non-retryable)
5. Multi-provider fallback support

SECURITY: Fail-closed design - if uncertain, reject the operation.
"""

import asyncio
import logging
import random
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import Enum, auto
from functools import wraps
from threading import Lock
from typing import (
    Any,
    Dict,
    List,
    Optional,
    TypeVar,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RPCErrorCategory(Enum):
    """Categories of RPC errors with different handling strategies."""

    RETRYABLE = auto()  # Temporary failures - retry with backoff
    RATE_LIMITED = auto()  # 429 errors - longer backoff
    NON_RETRYABLE = auto()  # Permanent failures - fail immediately
    TIMEOUT = auto()  # Connection/read timeout - retry with backoff
    CIRCUIT_OPEN = auto()  # Circuit breaker is open - fail fast


class RPCError(Exception):
    """Base exception for RPC errors with category classification."""

    def __init__(
        self,
        message: str,
        category: RPCErrorCategory = RPCErrorCategory.RETRYABLE,
        status_code: int | None = None,
        provider: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.provider = provider
        self.original_error = original_error

    def is_retryable(self) -> bool:
        """Check if this error should be retried."""
        return self.category in (
            RPCErrorCategory.RETRYABLE,
            RPCErrorCategory.RATE_LIMITED,
            RPCErrorCategory.TIMEOUT,
        )


class RateLimitError(RPCError):
    """Rate limit (429) error."""

    def __init__(
        self,
        message: str = "Rate limited",
        retry_after: float | None = None,
        provider: str | None = None,
    ):
        super().__init__(
            message,
            category=RPCErrorCategory.RATE_LIMITED,
            status_code=429,
            provider=provider,
        )
        self.retry_after = retry_after


class TimeoutError(RPCError):
    """Timeout error (connection or read)."""

    def __init__(
        self,
        message: str = "Request timed out",
        provider: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(
            message,
            category=RPCErrorCategory.TIMEOUT,
            provider=provider,
            original_error=original_error,
        )


class CircuitOpenError(RPCError):
    """Circuit breaker is open - too many recent failures."""

    def __init__(
        self,
        message: str = "Circuit breaker open",
        provider: str | None = None,
        reset_at: float | None = None,
    ):
        super().__init__(
            message,
            category=RPCErrorCategory.CIRCUIT_OPEN,
            provider=provider,
        )
        self.reset_at = reset_at


class NonRetryableError(RPCError):
    """Permanent error that should not be retried."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        provider: str | None = None,
        original_error: Exception | None = None,
    ):
        super().__init__(
            message,
            category=RPCErrorCategory.NON_RETRYABLE,
            status_code=status_code,
            provider=provider,
            original_error=original_error,
        )


@dataclass
class RetryPolicy:
    """
    Configurable retry policy for RPC operations.

    Attributes:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        exponential_base: Base for exponential backoff (e.g., 2.0)
        jitter_factor: Random jitter factor (0.0-1.0)
        rate_limit_multiplier: Extra multiplier for 429 errors
    """

    max_retries: int = 5
    base_delay: float = 0.5
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter_factor: float = 0.3
    rate_limit_multiplier: float = 3.0

    def calculate_delay(self, attempt: int, is_rate_limited: bool = False) -> float:
        """
        Calculate delay for a given attempt number.

        Args:
            attempt: Current attempt number (0-based)
            is_rate_limited: Whether this is a rate limit (429) error

        Returns:
            Delay in seconds with jitter applied
        """
        # Exponential backoff
        delay = self.base_delay * (self.exponential_base**attempt)

        # Apply rate limit multiplier
        if is_rate_limited:
            delay *= self.rate_limit_multiplier

        # Cap at max delay
        delay = min(delay, self.max_delay)

        # Add jitter
        jitter = delay * self.jitter_factor * random.random()
        delay += jitter

        return delay


# Default retry policies for different operation types
RETRY_POLICIES = {
    "default": RetryPolicy(),
    "transaction": RetryPolicy(
        max_retries=3,  # Fewer retries for transactions (time-sensitive)
        base_delay=0.3,
        max_delay=10.0,
    ),
    "metadata": RetryPolicy(
        max_retries=5,
        base_delay=0.5,
        max_delay=30.0,
    ),
    "balance": RetryPolicy(
        max_retries=4,
        base_delay=0.4,
        max_delay=20.0,
    ),
    "estimate_gas": RetryPolicy(
        max_retries=3,
        base_delay=0.3,
        max_delay=15.0,
    ),
}


@dataclass
class CircuitBreakerState:
    """State for a circuit breaker."""

    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float | None = None
    state: str = "closed"  # closed, open, half_open
    opened_at: float | None = None


class CircuitBreaker:
    """
    Circuit breaker pattern for RPC providers.

    Prevents cascading failures by failing fast when a provider
    is experiencing repeated failures.

    States:
    - CLOSED: Normal operation, requests go through
    - OPEN: Too many failures, requests fail immediately
    - HALF_OPEN: Testing if provider recovered

    SECURITY: Fail-closed - if circuit state is uncertain, reject.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 3,
        timeout_seconds: float = 60.0,
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Failures to open circuit
            success_threshold: Successes in half-open to close circuit
            timeout_seconds: Time before attempting recovery
        """
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout_seconds = timeout_seconds
        self._states: dict[str, CircuitBreakerState] = {}
        self._lock = Lock()

    def _get_state(self, provider: str) -> CircuitBreakerState:
        """Get or create state for a provider."""
        with self._lock:
            if provider not in self._states:
                self._states[provider] = CircuitBreakerState()
            return self._states[provider]

    def is_open(self, provider: str) -> bool:
        """Check if circuit is open (blocking requests)."""
        state = self._get_state(provider)

        with self._lock:
            if state.state == "closed":
                return False

            if state.state == "open":
                # Check if timeout has passed
                if state.opened_at and (time.time() - state.opened_at >= self.timeout_seconds):
                    # Transition to half-open
                    state.state = "half_open"
                    state.success_count = 0
                    logger.info(f"Circuit breaker for {provider} transitioning to HALF_OPEN")
                    return False
                return True

            # half_open - allow requests
            return False

    def record_success(self, provider: str) -> None:
        """Record a successful request."""
        state = self._get_state(provider)

        with self._lock:
            state.success_count += 1
            state.failure_count = 0

            if state.state == "half_open":
                if state.success_count >= self.success_threshold:
                    state.state = "closed"
                    logger.info(f"Circuit breaker for {provider} CLOSED (recovered)")

    def record_failure(self, provider: str) -> None:
        """Record a failed request."""
        state = self._get_state(provider)

        with self._lock:
            state.failure_count += 1
            state.success_count = 0
            state.last_failure_time = time.time()

            if state.state == "half_open":
                # Any failure in half-open opens the circuit
                state.state = "open"
                state.opened_at = time.time()
                logger.warning(f"Circuit breaker for {provider} OPEN (failed in half-open)")
            elif state.state == "closed" and state.failure_count >= self.failure_threshold:
                state.state = "open"
                state.opened_at = time.time()
                logger.warning(
                    f"Circuit breaker for {provider} OPEN (failures: {state.failure_count})"
                )

    def get_status(self, provider: str) -> dict[str, Any]:
        """Get current status for a provider."""
        state = self._get_state(provider)

        with self._lock:
            return {
                "provider": provider,
                "state": state.state,
                "failure_count": state.failure_count,
                "success_count": state.success_count,
                "last_failure_time": state.last_failure_time,
                "opened_at": state.opened_at,
            }

    def reset(self, provider: str) -> None:
        """Reset circuit breaker for a provider."""
        with self._lock:
            if provider in self._states:
                self._states[provider] = CircuitBreakerState()
                logger.info(f"Circuit breaker for {provider} RESET")

    def reset_all(self) -> None:
        """Reset all circuit breakers (for testing)."""
        with self._lock:
            self._states.clear()


# Global circuit breaker instance
circuit_breaker = CircuitBreaker()


def categorize_error(error: Exception, status_code: int | None = None) -> RPCErrorCategory:
    """
    Categorize an error to determine retry strategy.

    Args:
        error: The exception that occurred
        status_code: HTTP status code if available

    Returns:
        RPCErrorCategory indicating how to handle the error
    """
    error_str = str(error).lower()

    # Rate limiting
    if status_code == 429:
        return RPCErrorCategory.RATE_LIMITED

    # Non-retryable HTTP errors
    if status_code in (400, 401, 403, 404, 405):
        return RPCErrorCategory.NON_RETRYABLE

    # Timeout errors
    if any(keyword in error_str for keyword in ["timeout", "timed out", "connection reset", "eof"]):
        return RPCErrorCategory.TIMEOUT

    # Connection errors (retryable)
    if any(
        keyword in error_str
        for keyword in [
            "connection refused",
            "connection error",
            "network unreachable",
            "temporary failure",
            "service unavailable",
        ]
    ):
        return RPCErrorCategory.RETRYABLE

    # Server errors (retryable)
    if status_code and 500 <= status_code < 600:
        return RPCErrorCategory.RETRYABLE

    # RPC-specific non-retryable errors
    if any(
        keyword in error_str
        for keyword in [
            "invalid params",
            "method not found",
            "invalid request",
            "execution reverted",
            "gas too low",
            "nonce too low",
            "insufficient funds",
        ]
    ):
        return RPCErrorCategory.NON_RETRYABLE

    # Default to retryable for unknown errors
    return RPCErrorCategory.RETRYABLE


async def with_retry(
    func: Callable[..., Coroutine[Any, Any, T]],
    *args,
    policy: RetryPolicy | None = None,
    policy_name: str = "default",
    provider: str = "unknown",
    operation: str = "rpc_call",
    circuit: CircuitBreaker | None = None,
    **kwargs,
) -> T:
    """
    Execute an async function with retry logic and circuit breaker.

    Args:
        func: Async function to execute
        *args: Positional arguments for func
        policy: RetryPolicy to use (overrides policy_name)
        policy_name: Name of predefined policy (default, transaction, etc.)
        provider: Provider name for circuit breaker
        operation: Operation name for logging
        circuit: CircuitBreaker instance (uses global if None)
        **kwargs: Keyword arguments for func

    Returns:
        Result from func

    Raises:
        RPCError: If all retries exhausted or non-retryable error
        CircuitOpenError: If circuit breaker is open
    """
    if policy is None:
        policy = RETRY_POLICIES.get(policy_name, RETRY_POLICIES["default"])

    if circuit is None:
        circuit = circuit_breaker

    # Check circuit breaker
    if circuit.is_open(provider):
        state = circuit.get_status(provider)
        reset_at = state["opened_at"] + circuit.timeout_seconds if state["opened_at"] else None
        raise CircuitOpenError(
            f"Circuit breaker open for {provider}",
            provider=provider,
            reset_at=reset_at,
        )

    last_error: Exception | None = None

    for attempt in range(policy.max_retries + 1):
        try:
            result = await func(*args, **kwargs)
            circuit.record_success(provider)
            return result

        except asyncio.CancelledError:
            # Don't retry cancelled operations
            raise

        except Exception as e:
            last_error = e
            status_code = getattr(e, "status_code", None)
            category = categorize_error(e, status_code)

            # Non-retryable errors fail immediately
            if category == RPCErrorCategory.NON_RETRYABLE:
                circuit.record_failure(provider)
                raise NonRetryableError(
                    str(e),
                    status_code=status_code,
                    provider=provider,
                    original_error=e,
                )

            # Record failure for circuit breaker
            circuit.record_failure(provider)

            # Check if we should retry
            if attempt >= policy.max_retries:
                break

            # Calculate delay
            is_rate_limited = category == RPCErrorCategory.RATE_LIMITED
            delay = policy.calculate_delay(attempt, is_rate_limited)

            logger.warning(
                f"RPC {operation} failed (attempt {attempt + 1}/{policy.max_retries + 1}) "
                f"for {provider}: {e}. Retrying in {delay:.2f}s"
            )

            await asyncio.sleep(delay)

    # All retries exhausted
    raise RPCError(
        f"RPC {operation} failed after {policy.max_retries + 1} attempts: {last_error}",
        category=RPCErrorCategory.RETRYABLE,
        provider=provider,
        original_error=last_error,
    )


def retry_decorator(
    policy_name: str = "default",
    provider: str = "unknown",
):
    """
    Decorator for adding retry logic to async functions.

    Usage:
        @retry_decorator(policy_name="transaction", provider="infura")
        async def send_transaction(...):
            ...
    """

    def decorator(
        func: Callable[..., Coroutine[Any, Any, T]],
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            return await with_retry(
                func,
                *args,
                policy_name=policy_name,
                provider=provider,
                operation=func.__name__,
                **kwargs,
            )

        return wrapper

    return decorator


class MultiProviderRPC:
    """
    Multi-provider RPC client with automatic failover.

    Maintains multiple RPC providers and fails over when one becomes unavailable.
    Uses circuit breakers to track provider health.
    """

    def __init__(
        self,
        providers: dict[str, str],  # name -> url
        circuit: CircuitBreaker | None = None,
    ):
        """
        Initialize multi-provider RPC.

        Args:
            providers: Dict mapping provider name to RPC URL
            circuit: CircuitBreaker instance (uses global if None)
        """
        self.providers = providers
        self.provider_order = list(providers.keys())
        self.circuit = circuit or circuit_breaker

    def get_available_providers(self) -> list[str]:
        """Get list of available (non-open circuit) providers."""
        return [name for name in self.provider_order if not self.circuit.is_open(name)]

    async def call(
        self,
        func: Callable[[str], Coroutine[Any, Any, T]],
        operation: str = "rpc_call",
        policy_name: str = "default",
    ) -> T:
        """
        Call function with automatic provider failover.

        Args:
            func: Async function that takes RPC URL as first argument
            operation: Operation name for logging
            policy_name: Retry policy name

        Returns:
            Result from successful call

        Raises:
            RPCError: If all providers fail
        """
        available = self.get_available_providers()

        if not available:
            raise RPCError(
                "All RPC providers unavailable (circuits open)",
                category=RPCErrorCategory.CIRCUIT_OPEN,
            )

        errors: list[str] = []

        for provider_name in available:
            provider_url = self.providers[provider_name]

            try:
                return await with_retry(
                    func,
                    provider_url,
                    policy_name=policy_name,
                    provider=provider_name,
                    operation=operation,
                )
            except CircuitOpenError:
                # Provider circuit opened during retry, try next
                logger.warning(f"Provider {provider_name} circuit opened, trying next")
                errors.append(f"{provider_name}: circuit open")
                continue
            except NonRetryableError:
                # Non-retryable errors should not failover
                raise
            except RPCError as e:
                errors.append(f"{provider_name}: {e}")
                logger.warning(f"Provider {provider_name} failed for {operation}, trying next")
                continue

        raise RPCError(
            f"All providers failed for {operation}: {'; '.join(errors)}",
            category=RPCErrorCategory.RETRYABLE,
        )


def get_circuit_breaker() -> CircuitBreaker:
    """Get the global circuit breaker instance."""
    return circuit_breaker


def get_retry_policy(name: str) -> RetryPolicy:
    """Get a retry policy by name."""
    return RETRY_POLICIES.get(name, RETRY_POLICIES["default"])


__all__ = [
    "RETRY_POLICIES",
    "CircuitBreaker",
    "CircuitOpenError",
    "MultiProviderRPC",
    "NonRetryableError",
    "RPCError",
    "RPCErrorCategory",
    "RateLimitError",
    "RetryPolicy",
    "TimeoutError",
    "categorize_error",
    "circuit_breaker",
    "get_circuit_breaker",
    "get_retry_policy",
    "retry_decorator",
    "with_retry",
]
