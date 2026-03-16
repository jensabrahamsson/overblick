"""Tests for overblick.core.http_retry module."""

from unittest.mock import AsyncMock, patch

import pytest

from overblick.core.http_retry import retry_http, with_retry

# ---------------------------------------------------------------------------
# retry_http decorator tests
# ---------------------------------------------------------------------------


class TestRetryHttpDecorator:
    """Tests for the retry_http decorator."""

    async def test_should_return_result_when_call_succeeds_first_try(self):
        @retry_http(max_attempts=3)
        async def ok():
            return "success"

        assert await ok() == "success"

    async def test_should_retry_and_succeed_on_second_attempt(self):
        call_count = 0

        @retry_http(max_attempts=3, base_delay=0.0, jitter=0.0)
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("fail")
            return "ok"

        assert await flaky() == "ok"
        assert call_count == 2

    async def test_should_raise_after_max_attempts_exhausted(self):
        @retry_http(max_attempts=2, base_delay=0.0, jitter=0.0)
        async def always_fail():
            raise TimeoutError("timeout")

        with pytest.raises(TimeoutError, match="timeout"):
            await always_fail()

    async def test_should_reraise_non_retryable_exception_immediately(self):
        call_count = 0

        @retry_http(max_attempts=3, base_delay=0.0, jitter=0.0)
        async def bad():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            await bad()
        assert call_count == 1

    async def test_should_use_custom_retry_exceptions(self):
        call_count = 0

        @retry_http(
            max_attempts=3,
            base_delay=0.0,
            jitter=0.0,
            retry_exceptions=[ValueError],
        )
        async def custom():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("retryable")
            return "done"

        assert await custom() == "done"
        assert call_count == 2

    async def test_should_respect_max_delay_cap(self):
        """Delay should be capped at max_delay."""
        delays: list[float] = []

        @retry_http(max_attempts=5, base_delay=10.0, max_delay=15.0, jitter=0.0)
        async def always_fail():
            raise ConnectionError("fail")

        with patch("overblick.core.http_retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(ConnectionError):
                await always_fail()
            # 4 sleeps (5 attempts - 1)
            for call in mock_sleep.call_args_list:
                delays.append(call[0][0])
            # base_delay=10, 2nd attempt delay = 10*1=10, 3rd = 10*2=20 capped to 15, etc.
            assert delays[0] == 10.0
            assert delays[1] == 15.0  # capped
            assert delays[2] == 15.0  # capped
            assert delays[3] == 15.0  # capped

    async def test_should_apply_jitter_to_delay(self):
        """Jitter should modify the delay."""

        @retry_http(max_attempts=2, base_delay=1.0, max_delay=30.0, jitter=0.5)
        async def fail():
            raise OSError("fail")

        with patch("overblick.core.http_retry.random.random", return_value=1.0):
            with patch("overblick.core.http_retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                with pytest.raises(OSError):
                    await fail()
                # jitter_amount = 1.0 * 0.5 * (1.0 * 2 - 1) = 0.5
                # delay = 1.0 + 0.5 = 1.5
                assert mock_sleep.call_args[0][0] == 1.5

    async def test_should_clamp_delay_to_zero_when_jitter_goes_negative(self):
        """Delay should never go negative."""

        @retry_http(max_attempts=2, base_delay=0.01, max_delay=30.0, jitter=1.0)
        async def fail():
            raise ConnectionError("fail")

        # random() returns 0.0 -> jitter_amount = 0.01 * 1.0 * (0*2 - 1) = -0.01
        # delay = max(0.0, 0.01 + (-0.01)) = 0.0
        with patch("overblick.core.http_retry.random.random", return_value=0.0):
            with patch("overblick.core.http_retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                with pytest.raises(ConnectionError):
                    await fail()
                assert mock_sleep.call_args[0][0] == 0.0

    async def test_should_pass_args_and_kwargs_to_wrapped_function(self):
        @retry_http(max_attempts=1)
        async def func(a, b, c=None):
            return (a, b, c)

        assert await func(1, 2, c=3) == (1, 2, 3)

    async def test_should_preserve_function_name(self):
        @retry_http()
        async def my_function():
            pass

        assert my_function.__name__ == "my_function"

    async def test_should_log_warning_on_final_failure(self):
        @retry_http(max_attempts=1, base_delay=0.0, jitter=0.0)
        async def fail():
            raise ConnectionError("final")

        with patch("overblick.core.http_retry.logger") as mock_logger:
            with pytest.raises(ConnectionError):
                await fail()
            mock_logger.warning.assert_called_once()

    async def test_should_log_debug_on_retry(self):
        call_count = 0

        @retry_http(max_attempts=2, base_delay=0.0, jitter=0.0)
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("retry me")
            return "ok"

        with patch("overblick.core.http_retry.logger") as mock_logger:
            await flaky()
            mock_logger.debug.assert_called_once()

    async def test_should_handle_asyncio_timeout_error(self):
        """asyncio.TimeoutError is in default retry list."""
        call_count = 0

        @retry_http(max_attempts=2, base_delay=0.0, jitter=0.0)
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise TimeoutError()
            return "ok"

        assert await flaky() == "ok"
        assert call_count == 2

    async def test_should_use_default_retry_exceptions_when_none(self):
        """When retry_exceptions is None, defaults are used."""
        call_count = 0

        @retry_http(max_attempts=2, base_delay=0.0, jitter=0.0, retry_exceptions=None)
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OSError("dns fail")
            return "ok"

        assert await flaky() == "ok"

    async def test_should_raise_none_when_max_attempts_is_zero(self):
        """Edge case: max_attempts=0 means the loop body never runs, hitting the fallback raise."""

        @retry_http(max_attempts=0)
        async def never_called():
            return "should not reach"  # pragma: no cover

        with pytest.raises(TypeError):
            await never_called()


# ---------------------------------------------------------------------------
# with_retry function tests
# ---------------------------------------------------------------------------


class TestWithRetry:
    """Tests for the with_retry convenience function."""

    async def test_should_return_result_when_call_succeeds(self):
        async def ok():
            return 42

        result = await with_retry(ok, max_attempts=3)
        assert result == 42

    async def test_should_retry_and_succeed(self):
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("fail")
            return "ok"

        result = await with_retry(flaky, max_attempts=3, base_delay=0.0, jitter=0.0)
        assert result == "ok"
        assert call_count == 2

    async def test_should_raise_after_max_attempts(self):
        async def fail():
            raise TimeoutError("always fails")

        with pytest.raises(TimeoutError, match="always fails"):
            await with_retry(fail, max_attempts=2, base_delay=0.0, jitter=0.0)

    async def test_should_reraise_non_retryable_immediately(self):
        call_count = 0

        async def bad():
            nonlocal call_count
            call_count += 1
            raise ValueError("nope")

        with pytest.raises(ValueError, match="nope"):
            await with_retry(bad, max_attempts=3, base_delay=0.0, jitter=0.0)
        assert call_count == 1

    async def test_should_use_custom_retry_exceptions(self):
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise KeyError("retryable")
            return "done"

        result = await with_retry(
            flaky,
            max_attempts=3,
            base_delay=0.0,
            jitter=0.0,
            retry_exceptions=[KeyError],
        )
        assert result == "done"
        assert call_count == 2

    async def test_should_use_default_exceptions_when_none(self):
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OSError("dns")
            return "ok"

        result = await with_retry(flaky, max_attempts=2, base_delay=0.0, jitter=0.0, retry_exceptions=None)
        assert result == "ok"

    async def test_should_cap_delay_at_max_delay(self):
        async def fail():
            raise ConnectionError("fail")

        with patch("overblick.core.http_retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(ConnectionError):
                await with_retry(fail, max_attempts=4, base_delay=10.0, max_delay=15.0, jitter=0.0)
            delays = [c[0][0] for c in mock_sleep.call_args_list]
            assert delays == [10.0, 15.0, 15.0]

    async def test_should_apply_jitter(self):
        async def fail():
            raise ConnectionError("fail")

        with patch("overblick.core.http_retry.random.random", return_value=1.0):
            with patch("overblick.core.http_retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                with pytest.raises(ConnectionError):
                    await with_retry(fail, max_attempts=2, base_delay=1.0, jitter=0.5)
                assert mock_sleep.call_args[0][0] == 1.5

    async def test_should_clamp_negative_delay_to_zero(self):
        async def fail():
            raise ConnectionError("fail")

        with patch("overblick.core.http_retry.random.random", return_value=0.0):
            with patch("overblick.core.http_retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                with pytest.raises(ConnectionError):
                    await with_retry(fail, max_attempts=2, base_delay=0.01, jitter=1.0)
                assert mock_sleep.call_args[0][0] == 0.0

    async def test_should_log_warning_on_final_failure(self):
        async def fail():
            raise ConnectionError("final")

        with patch("overblick.core.http_retry.logger") as mock_logger:
            with pytest.raises(ConnectionError):
                await with_retry(fail, max_attempts=1, base_delay=0.0, jitter=0.0)
            mock_logger.warning.assert_called_once()

    async def test_should_log_debug_on_retry(self):
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("retry")
            return "ok"

        with patch("overblick.core.http_retry.logger") as mock_logger:
            await with_retry(flaky, max_attempts=2, base_delay=0.0, jitter=0.0)
            mock_logger.debug.assert_called_once()

    async def test_should_raise_none_when_max_attempts_is_zero(self):
        """Edge case: max_attempts=0 means the loop never executes, hitting fallback raise."""

        async def never_called():
            return "should not reach"  # pragma: no cover

        with pytest.raises(TypeError):
            await with_retry(never_called, max_attempts=0)
