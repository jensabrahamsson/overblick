"""Tests for the System Health dashboard page and metrics partial."""

import pytest
from unittest.mock import AsyncMock, patch

from overblick.dashboard.auth import SESSION_COOKIE
from overblick.capabilities.monitoring.models import (
    HostHealth, MemoryInfo, CPUInfo, PowerInfo,
)


def _fake_host_health() -> HostHealth:
    """Build a realistic HostHealth for testing."""
    return HostHealth(
        hostname="testhost.local",
        platform="darwin",
        uptime="3 days, 2:15",
        memory=MemoryInfo(total_mb=16384.0, used_mb=10240.0, available_mb=6144.0, percent_used=62.5),
        cpu=CPUInfo(load_1m=2.1, load_5m=1.8, load_15m=1.5, core_count=8),
        power=PowerInfo(on_battery=False, battery_percent=85.0, time_remaining=None),
        errors=[],
    )


def _fake_gateway_health() -> dict:
    """Build a realistic Gateway /health response."""
    return {
        "status": "healthy",
        "gateway": "running",
        "ollama": "connected",
        "queue_size": 2,
        "gpu_starvation_risk": "low",
        "avg_response_time_ms": 1234.5,
        "active_requests": 1,
    }


_HEALTH_FN = "overblick.dashboard.routes.system._collect_host_health"
_GATEWAY_FN = "overblick.dashboard.routes.system._fetch_gateway_health"


class TestSystemPage:
    @pytest.mark.asyncio
    async def test_system_renders_authenticated(self, client, session_cookie):
        """Authenticated user sees the system health page."""
        cookie_value, _ = session_cookie
        with patch(_HEALTH_FN, new_callable=AsyncMock, return_value=_fake_host_health()), \
             patch(_GATEWAY_FN, new_callable=AsyncMock, return_value=_fake_gateway_health()):
            resp = await client.get("/system", cookies={SESSION_COOKIE: cookie_value})

        assert resp.status_code == 200
        assert "System Health" in resp.text

    @pytest.mark.asyncio
    async def test_system_redirects_unauthenticated(self, client):
        """Unauthenticated user is redirected to login."""
        resp = await client.get("/system", follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_system_shows_hostname(self, client, session_cookie):
        """Page displays host information."""
        cookie_value, _ = session_cookie
        with patch(_HEALTH_FN, new_callable=AsyncMock, return_value=_fake_host_health()), \
             patch(_GATEWAY_FN, new_callable=AsyncMock, return_value=_fake_gateway_health()):
            resp = await client.get("/system", cookies={SESSION_COOKIE: cookie_value})

        assert "testhost.local" in resp.text
        assert "GOOD" in resp.text

    @pytest.mark.asyncio
    async def test_system_shows_gauges(self, client, session_cookie):
        """Page displays RAM, CPU, and Battery gauges."""
        cookie_value, _ = session_cookie
        with patch(_HEALTH_FN, new_callable=AsyncMock, return_value=_fake_host_health()), \
             patch(_GATEWAY_FN, new_callable=AsyncMock, return_value=_fake_gateway_health()):
            resp = await client.get("/system", cookies={SESSION_COOKIE: cookie_value})

        assert "RAM" in resp.text
        assert "CPU" in resp.text
        assert "Battery" in resp.text

    @pytest.mark.asyncio
    async def test_system_shows_gateway_stats(self, client, session_cookie):
        """Page displays LLM Gateway status when available."""
        cookie_value, _ = session_cookie
        with patch(_HEALTH_FN, new_callable=AsyncMock, return_value=_fake_host_health()), \
             patch(_GATEWAY_FN, new_callable=AsyncMock, return_value=_fake_gateway_health()):
            resp = await client.get("/system", cookies={SESSION_COOKIE: cookie_value})

        assert "LLM Gateway" in resp.text
        assert "healthy" in resp.text
        assert "1234" in resp.text


class TestSystemMetricsPartial:
    @pytest.mark.asyncio
    async def test_metrics_partial_returns_html(self, client, session_cookie):
        """Metrics partial returns valid HTML fragment."""
        cookie_value, _ = session_cookie
        with patch(_HEALTH_FN, new_callable=AsyncMock, return_value=_fake_host_health()), \
             patch(_GATEWAY_FN, new_callable=AsyncMock, return_value=_fake_gateway_health()):
            resp = await client.get("/system/metrics", cookies={SESSION_COOKIE: cookie_value})

        assert resp.status_code == 200
        assert "gauge-svg" in resp.text

    @pytest.mark.asyncio
    async def test_metrics_partial_no_battery_on_desktop(self, client, session_cookie):
        """Battery gauge is hidden when battery_percent is None."""
        cookie_value, _ = session_cookie
        health = _fake_host_health()
        health.power = PowerInfo()  # No battery info

        with patch(_HEALTH_FN, new_callable=AsyncMock, return_value=health), \
             patch(_GATEWAY_FN, new_callable=AsyncMock, return_value=_fake_gateway_health()):
            resp = await client.get("/system/metrics", cookies={SESSION_COOKIE: cookie_value})

        assert "gauge-sublabel\">Battery</text>" not in resp.text


class TestSystemGracefulDegradation:
    @pytest.mark.asyncio
    async def test_gateway_unavailable(self, client, session_cookie):
        """Page shows 'Unavailable' when Gateway is down."""
        cookie_value, _ = session_cookie
        with patch(_HEALTH_FN, new_callable=AsyncMock, return_value=_fake_host_health()), \
             patch(_GATEWAY_FN, new_callable=AsyncMock, return_value=None):
            resp = await client.get("/system", cookies={SESSION_COOKIE: cookie_value})

        assert resp.status_code == 200
        assert "Unavailable" in resp.text

    @pytest.mark.asyncio
    async def test_inspect_failure_returns_defaults(self, client, session_cookie):
        """Page still renders when host inspection fails."""
        cookie_value, _ = session_cookie
        with patch(_HEALTH_FN, new_callable=AsyncMock, return_value=HostHealth()), \
             patch(_GATEWAY_FN, new_callable=AsyncMock, return_value=_fake_gateway_health()):
            resp = await client.get("/system", cookies={SESSION_COOKIE: cookie_value})

        assert resp.status_code == 200
        assert "System Health" in resp.text
