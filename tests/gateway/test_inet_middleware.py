"""Tests for Internet Gateway middleware."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from overblick.gateway.inet_middleware import (
    GlobalRateLimitMiddleware,
    IPAllowlistMiddleware,
    IPBanMiddleware,
    RequestSizeLimitMiddleware,
    ViolationTracker,
    _error_response,
    _extract_client_ip,
)


class TestViolationTracker:
    """Tests for the ViolationTracker class."""

    def test_record_violation_below_threshold(self):
        tracker = ViolationTracker(threshold=5, window_seconds=300, ban_duration=3600)
        for _ in range(4):
            result = tracker.record_violation("1.2.3.4")
            assert result is False
        assert tracker.is_banned("1.2.3.4") is False

    def test_record_violation_at_threshold_triggers_ban(self):
        tracker = ViolationTracker(threshold=5, window_seconds=300, ban_duration=3600)
        for i in range(5):
            result = tracker.record_violation("1.2.3.4")
        assert result is True
        assert tracker.is_banned("1.2.3.4") is True

    def test_ban_expires(self):
        tracker = ViolationTracker(threshold=2, window_seconds=300, ban_duration=1)

        tracker.record_violation("1.2.3.4")
        tracker.record_violation("1.2.3.4")

        assert tracker.is_banned("1.2.3.4") is True

        # Manually expire the ban
        tracker._bans["1.2.3.4"] = time.time() - 1
        assert tracker.is_banned("1.2.3.4") is False

    def test_different_ips_independent(self):
        tracker = ViolationTracker(threshold=3, window_seconds=300, ban_duration=3600)

        for _ in range(3):
            tracker.record_violation("1.1.1.1")

        assert tracker.is_banned("1.1.1.1") is True
        assert tracker.is_banned("2.2.2.2") is False

    def test_ban_remaining(self):
        tracker = ViolationTracker(threshold=2, window_seconds=300, ban_duration=10)

        tracker.record_violation("1.2.3.4")
        tracker.record_violation("1.2.3.4")

        remaining = tracker.ban_remaining("1.2.3.4")
        assert 0 < remaining <= 10

        # Not banned
        assert tracker.ban_remaining("2.2.2.2") == 0

    def test_cleanup_removes_expired_bans(self):
        tracker = ViolationTracker(threshold=2, window_seconds=300, ban_duration=1)

        tracker.record_violation("1.2.3.4")
        tracker.record_violation("1.2.3.4")

        # Manually expire the ban
        tracker._bans["1.2.3.4"] = time.time() - 2

        removed = tracker.cleanup()
        assert removed >= 1
        assert tracker.is_banned("1.2.3.4") is False

    def test_evict_oldest_when_at_capacity(self):
        tracker = ViolationTracker(
            threshold=5, window_seconds=300, ban_duration=3600, max_tracked_ips=3
        )

        # Add 5 IPs (exceeds max_tracked_ips)
        for i in range(5):
            tracker.record_violation(f"1.2.3.{i}")

        # Should have evicted some old entries
        assert len(tracker._violations) <= 3


class TestRequestSizeLimitMiddleware:
    """Tests for RequestSizeLimitMiddleware."""

    @pytest.mark.asyncio
    async def test_valid_content_length(self):
        middleware = RequestSizeLimitMiddleware(app=AsyncMock(), max_bytes=1000)
        request = MagicMock(spec=Request)
        request.headers = {"content-length": "500"}
        request.client = MagicMock(host="1.2.3.4")

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_content_length_too_large(self):
        middleware = RequestSizeLimitMiddleware(app=AsyncMock(), max_bytes=1000)
        request = MagicMock(spec=Request)
        request.headers = {"content-length": "1500"}
        request.client = MagicMock(host="1.2.3.4")

        call_next = AsyncMock()
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 413
        assert "too large" in response.body.decode().lower()
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_content_length(self):
        middleware = RequestSizeLimitMiddleware(app=AsyncMock(), max_bytes=1000)
        request = MagicMock(spec=Request)
        request.headers = {"content-length": "not-a-number"}
        request.client = MagicMock(host="1.2.3.4")

        call_next = AsyncMock()
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 400
        assert "invalid content-length" in response.body.decode().lower()
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_content_length_allowed(self):
        middleware = RequestSizeLimitMiddleware(app=AsyncMock(), max_bytes=1000)
        request = MagicMock(spec=Request)
        request.headers = {}
        request.client = MagicMock(host="1.2.3.4")

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once_with(request)


class TestIPBanMiddleware:
    """Tests for IPBanMiddleware."""

    @pytest.mark.asyncio
    async def test_allowed_ip(self):
        tracker = ViolationTracker(threshold=5, window_seconds=300, ban_duration=3600)
        middleware = IPBanMiddleware(app=AsyncMock(), tracker=tracker)

        request = MagicMock(spec=Request)
        request.client = MagicMock(host="1.2.3.4")

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_banned_ip_rejected(self):
        tracker = ViolationTracker(threshold=2, window_seconds=300, ban_duration=3600)
        tracker.record_violation("1.2.3.4")
        tracker.record_violation("1.2.3.4")

        middleware = IPBanMiddleware(app=AsyncMock(), tracker=tracker)

        request = MagicMock(spec=Request)
        request.client = MagicMock(host="1.2.3.4")

        call_next = AsyncMock()
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 403
        assert "access denied" in response.body.decode().lower()
        assert "Retry-After" in response.headers
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_client_ip_allowed(self):
        tracker = ViolationTracker(threshold=5, window_seconds=300, ban_duration=3600)
        middleware = IPBanMiddleware(app=AsyncMock(), tracker=tracker)

        request = MagicMock(spec=Request)
        request.client = None  # No client info

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once_with(request)


class TestIPAllowlistMiddleware:
    """Tests for IPAllowlistMiddleware."""

    @pytest.mark.asyncio
    async def test_empty_allowlist_allows_all(self):
        middleware = IPAllowlistMiddleware(app=AsyncMock(), allowlist=[])

        request = MagicMock(spec=Request)
        request.client = MagicMock(host="1.2.3.4")

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_ip_in_allowlist_allowed(self):
        middleware = IPAllowlistMiddleware(app=AsyncMock(), allowlist=["192.168.1.0/24"])

        request = MagicMock(spec=Request)
        request.client = MagicMock(host="192.168.1.100")

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_ip_not_in_allowlist_rejected(self):
        middleware = IPAllowlistMiddleware(app=AsyncMock(), allowlist=["192.168.1.0/24"])

        request = MagicMock(spec=Request)
        request.client = MagicMock(host="10.0.0.1")

        call_next = AsyncMock()
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 403
        assert "access denied" in response.body.decode().lower()
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_ip_address_rejected(self):
        middleware = IPAllowlistMiddleware(app=AsyncMock(), allowlist=["192.168.1.0/24"])

        request = MagicMock(spec=Request)
        request.client = MagicMock(host="not-an-ip")

        call_next = AsyncMock()
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 403
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_cidr_network_matching(self):
        middleware = IPAllowlistMiddleware(app=AsyncMock(), allowlist=["10.0.0.0/8"])

        request = MagicMock(spec=Request)
        request.client = MagicMock(host="10.1.2.3")

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once_with(request)


class TestGlobalRateLimitMiddleware:
    """Tests for GlobalRateLimitMiddleware."""

    @pytest.mark.asyncio
    async def test_health_check_separate_limit(self):
        middleware = GlobalRateLimitMiddleware(app=AsyncMock(), rpm=1)

        # Make many health requests (should all succeed due to separate limit)
        request = MagicMock(spec=Request)
        request.url.path = "/health"
        request.client = MagicMock(host="1.2.3.4")

        call_next = AsyncMock(return_value=JSONResponse({"status": "ok"}))

        for _ in range(5):
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self):
        middleware = GlobalRateLimitMiddleware(app=AsyncMock(), rpm=1)

        request = MagicMock(spec=Request)
        request.url.path = "/v1/chat/completions"
        request.client = MagicMock(host="1.2.3.4")

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))

        # First request should succeed
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200
        assert "X-RateLimit-Limit" in response.headers

        # Second request should be rate limited
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 429
        assert "rate limit" in response.body.decode().lower()
        assert "Retry-After" in response.headers

    @pytest.mark.asyncio
    async def test_rate_limit_headers(self):
        middleware = GlobalRateLimitMiddleware(app=AsyncMock(), rpm=60)

        request = MagicMock(spec=Request)
        request.url.path = "/v1/chat/completions"
        request.client = MagicMock(host="1.2.3.4")

        call_next = AsyncMock(return_value=JSONResponse({"ok": True}))
        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        assert response.headers["X-RateLimit-Limit"] == "60"


class TestExtractClientIP:
    """Tests for _extract_client_ip with trusted proxies."""

    def test_should_return_client_host_when_no_trusted_proxies(self):
        request = MagicMock(spec=Request)
        request.client = MagicMock(host="10.0.0.1")

        result = _extract_client_ip(request, trusted_proxies=[])
        assert result == "10.0.0.1"

    def test_should_return_unknown_when_no_client_and_no_proxies(self):
        request = MagicMock(spec=Request)
        request.client = None

        result = _extract_client_ip(request, trusted_proxies=[])
        assert result == "unknown"

    def test_should_return_unknown_when_no_client_with_proxies(self):
        request = MagicMock(spec=Request)
        request.client = None

        result = _extract_client_ip(request, trusted_proxies=["10.0.0.0/8"])
        assert result == "unknown"

    def test_should_extract_ip_from_forwarded_for_with_trusted_proxy(self):
        request = MagicMock(spec=Request)
        request.client = MagicMock(host="10.0.0.1")
        request.headers = {"x-forwarded-for": "203.0.113.50, 10.0.0.1"}

        result = _extract_client_ip(request, trusted_proxies=["10.0.0.0/8"])
        assert result == "203.0.113.50"

    def test_should_ignore_forwarded_for_when_remote_not_trusted(self):
        request = MagicMock(spec=Request)
        request.client = MagicMock(host="1.2.3.4")
        request.headers = {"x-forwarded-for": "spoofed.ip"}

        result = _extract_client_ip(request, trusted_proxies=["10.0.0.0/8"])
        assert result == "1.2.3.4"

    def test_should_handle_chain_of_trusted_proxies(self):
        request = MagicMock(spec=Request)
        request.client = MagicMock(host="10.0.0.2")
        request.headers = {"x-forwarded-for": "203.0.113.1, 10.0.0.5, 10.0.0.3"}

        result = _extract_client_ip(request, trusted_proxies=["10.0.0.0/8"])
        assert result == "203.0.113.1"

    def test_should_return_leftmost_when_all_trusted(self):
        request = MagicMock(spec=Request)
        request.client = MagicMock(host="10.0.0.2")
        request.headers = {"x-forwarded-for": "10.0.0.1, 10.0.0.3"}

        result = _extract_client_ip(request, trusted_proxies=["10.0.0.0/8"])
        assert result == "10.0.0.1"

    def test_should_handle_invalid_cidr_in_trusted_proxies(self):
        request = MagicMock(spec=Request)
        request.client = MagicMock(host="1.2.3.4")
        request.headers = {}

        result = _extract_client_ip(request, trusted_proxies=["not-a-cidr"])
        assert result == "1.2.3.4"

    def test_should_handle_invalid_ip_in_forwarded_chain(self):
        request = MagicMock(spec=Request)
        request.client = MagicMock(host="10.0.0.1")
        request.headers = {"x-forwarded-for": "invalid-ip"}

        # invalid-ip is not trusted (is_trusted returns False for invalid IPs),
        # so it's returned as the client IP
        result = _extract_client_ip(request, trusted_proxies=["10.0.0.0/8"])
        assert result == "invalid-ip"

    def test_should_return_unknown_when_chain_empty_and_all_trusted(self):
        request = MagicMock(spec=Request)
        request.client = MagicMock(host="10.0.0.1")
        request.headers = {"x-forwarded-for": ""}

        result = _extract_client_ip(request, trusted_proxies=["10.0.0.0/8"])
        # chain is ["10.0.0.1"], all trusted, return chain[0]
        assert result == "10.0.0.1"


class TestViolationTrackerWithBanStore:
    """Tests for ViolationTracker with persistent ban store."""

    def test_should_load_bans_from_store_on_init(self):
        mock_store = MagicMock()
        mock_store.load_bans.return_value = {"5.5.5.5": time.time() + 3600}

        tracker = ViolationTracker(
            threshold=5, window_seconds=300, ban_duration=3600, ban_store=mock_store
        )

        assert tracker.is_banned("5.5.5.5") is True
        mock_store.load_bans.assert_called_once()

    def test_should_persist_ban_to_store_when_threshold_reached(self):
        mock_store = MagicMock()
        mock_store.load_bans.return_value = {}

        tracker = ViolationTracker(
            threshold=2, window_seconds=300, ban_duration=3600, ban_store=mock_store
        )
        tracker.record_violation("1.2.3.4")
        tracker.record_violation("1.2.3.4")

        mock_store.add_ban.assert_called_once()
        assert mock_store.add_ban.call_args[0][0] == "1.2.3.4"

    def test_should_remove_ban_from_store_on_expiry_check(self):
        mock_store = MagicMock()
        mock_store.load_bans.return_value = {"1.2.3.4": time.time() - 1}

        tracker = ViolationTracker(
            threshold=5, window_seconds=300, ban_duration=3600, ban_store=mock_store
        )

        # is_banned should detect expiry and remove from store
        assert tracker.is_banned("1.2.3.4") is False
        mock_store.remove_ban.assert_called_once_with("1.2.3.4")

    def test_should_call_store_cleanup_on_cleanup(self):
        mock_store = MagicMock()
        mock_store.load_bans.return_value = {}
        mock_store.cleanup_expired.return_value = 3

        tracker = ViolationTracker(
            threshold=5, window_seconds=300, ban_duration=3600, ban_store=mock_store
        )
        removed = tracker.cleanup()
        assert removed >= 3
        mock_store.cleanup_expired.assert_called_once()

    def test_should_cleanup_stale_violations(self):
        tracker = ViolationTracker(threshold=5, window_seconds=1, ban_duration=3600)
        tracker.record_violation("1.2.3.4")

        # Manually make violation old
        tracker._violations["1.2.3.4"] = [time.time() - 10]

        removed = tracker.cleanup()
        assert removed >= 1
        assert "1.2.3.4" not in tracker._violations

    def test_should_close_ban_store(self):
        mock_store = MagicMock()
        mock_store.load_bans.return_value = {}

        tracker = ViolationTracker(
            threshold=5, window_seconds=300, ban_duration=3600, ban_store=mock_store
        )
        tracker.close()
        mock_store.close.assert_called_once()

    def test_should_close_without_ban_store(self):
        tracker = ViolationTracker(threshold=5, window_seconds=300, ban_duration=3600)
        tracker.close()  # should not raise


class TestGlobalRateLimitHealthExceeded:
    """Tests for health endpoint rate limit exhaustion."""

    @pytest.mark.asyncio
    async def test_should_reject_health_when_rate_limit_exceeded(self):
        middleware = GlobalRateLimitMiddleware(app=AsyncMock(), rpm=60)

        request = MagicMock(spec=Request)
        request.url.path = "/health"
        request.client = MagicMock(host="1.2.3.4")

        call_next = AsyncMock(return_value=JSONResponse({"status": "ok"}))

        # Exhaust the health rate limiter (300 RPM = 300 tokens)
        for _ in range(300):
            await middleware.dispatch(request, call_next)

        # Next request should be rate limited
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 429


class TestErrorResponse:
    """Tests for the _error_response helper."""

    def test_error_response_format(self):
        response = _error_response(400, "Invalid request", "invalid_request_error")

        assert response.status_code == 400
        data = response.body.decode()
        assert "invalid request" in data.lower()
        assert "invalid_request_error" in data
        assert "code" in data
