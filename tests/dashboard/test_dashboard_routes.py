"""Tests for dashboard main page and partials."""

import pytest
from overblick.dashboard.auth import SESSION_COOKIE


class TestDashboardPage:
    @pytest.mark.asyncio
    async def test_dashboard_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Agents" in resp.text
        assert "Recent Activity" in resp.text

    @pytest.mark.asyncio
    async def test_dashboard_shows_agent_cards(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert "Anomal" in resp.text
        assert "Cherry" in resp.text

    @pytest.mark.asyncio
    async def test_dashboard_shows_system_health(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert "Supervisor" in resp.text
        assert "Identities" in resp.text


class TestDashboardPartials:
    @pytest.mark.asyncio
    async def test_plugin_cards_partial(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/partials/plugin-cards",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Anomal" in resp.text

    @pytest.mark.asyncio
    async def test_system_health_partial(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/partials/system-health",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Supervisor" in resp.text

    @pytest.mark.asyncio
    async def test_audit_recent_partial(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/partials/audit-recent",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "api_call" in resp.text
