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


class TestErrorResponse:
    """Tests for the _error_response helper."""

    def test_error_response_format(self):
        response = _error_response(400, "Invalid request", "invalid_request_error")

        assert response.status_code == 400
        data = response.body.decode()
        assert "invalid request" in data.lower()
        assert "invalid_request_error" in data
        assert "code" in data
