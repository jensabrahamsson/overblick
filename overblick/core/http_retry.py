"""
Shared HTTP retry wrapper — exponential backoff with jitter for network requests.

Provides a decorator and context manager for retrying failed HTTP calls,
with configurable attempts, delays, and exception detection.

Usage:
    from overblick.core.http_retry import retry_http

    @retry_http(max_attempts=3)
    async def fetch(url):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                return await resp.json()

    # Or as a wrapper
    result = await retry_http(lambda: call_api(), max_attempts=5)
"""

import asyncio
import random
import logging
from functools import wraps
from typing import Type, Callable, Any, Optional, Union, Sequence

logger = logging.getLogger(__name__)

# Default exceptions that trigger a retry (network errors, timeouts)
_DEFAULT_RETRY_EXCEPTIONS: tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
    OSError,  # socket errors, DNS resolution, etc.
)


def retry_http(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.1,
    retry_exceptions: Optional[Sequence[Type[Exception]]] = None,
) -> Callable:
    """
    Decorator for retrying HTTP calls with exponential backoff and jitter.

    Args:
        max_attempts: Maximum number of attempts (including first try).
        base_delay: Base delay in seconds (doubles each retry).
        max_delay: Maximum delay in seconds (caps exponential growth).
        jitter: Random jitter factor (0.0 to 1.0) to avoid thundering herd.
        retry_exceptions: Exception types that trigger a retry.
            Defaults to network errors (ConnectionError, TimeoutError, OSError).

    Returns:
        Decorator that can be applied to async functions.
    """
    if retry_exceptions is None:
        retry_exceptions = _DEFAULT_RETRY_EXCEPTIONS

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    # Check if exception is in retryable list
                    if not any(isinstance(e, exc_type) for exc_type in retry_exceptions):
                        raise  # Non-retryable exception, re-raise immediately

                    last_exception = e
                    if attempt == max_attempts:
                        logger.warning(
                            "HTTP call failed after %d attempts: %s",
                            max_attempts,
                            e,
                            exc_info=True,
                        )
                        raise

                    # Calculate delay with exponential backoff and jitter
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    jitter_amount = delay * jitter * (random.random() * 2 - 1)  # ±jitter
                    delay = max(0.0, delay + jitter_amount)

                    logger.debug(
                        "HTTP call failed (attempt %d/%d), retrying in %.2fs: %s",
                        attempt,
                        max_attempts,
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)

            # Should never reach here (loop either returns or raises)
            raise last_exception  # type: ignore

        return wrapper

    return decorator


async def with_retry(
    coro_func: Callable[[], Any],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: float = 0.1,
    retry_exceptions: Optional[Sequence[Type[Exception]]] = None,
) -> Any:
    """
    Retry a coroutine function with exponential backoff.

    Convenience wrapper for one-off calls.

    Args:
        coro_func: A callable that returns an awaitable (no arguments).
        max_attempts: Maximum number of attempts (including first try).
        base_delay: Base delay in seconds (doubles each retry).
        max_delay: Maximum delay in seconds (caps exponential growth).
        jitter: Random jitter factor (0.0 to 1.0) to avoid thundering herd.
        retry_exceptions: Exception types that trigger a retry.

    Returns:
        Result of the coroutine.

    Raises:
        Last exception if all attempts fail.
    """
    if retry_exceptions is None:
        retry_exceptions = _DEFAULT_RETRY_EXCEPTIONS

    last_exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_func()
        except Exception as e:
            if not any(isinstance(e, exc_type) for exc_type in retry_exceptions):
                raise
            last_exception = e
            if attempt == max_attempts:
                logger.warning(
                    "HTTP call failed after %d attempts: %s",
                    max_attempts,
                    e,
                    exc_info=True,
                )
                raise

            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            jitter_amount = delay * jitter * (random.random() * 2 - 1)
            delay = max(0.0, delay + jitter_amount)

            logger.debug(
                "HTTP call failed (attempt %d/%d), retrying in %.2fs: %s",
                attempt,
                max_attempts,
                delay,
                e,
            )
            await asyncio.sleep(delay)

    raise last_exception  # type: ignore
