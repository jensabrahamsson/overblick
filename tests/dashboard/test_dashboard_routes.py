"""Tests for dashboard main page, partials, and agent actions."""

import pytest
from unittest.mock import AsyncMock
from overblick.dashboard.auth import SESSION_COOKIE


class TestDashboardPage:
    @pytest.mark.asyncio
    async def test_dashboard_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Agent Status" in resp.text

    @pytest.mark.asyncio
    async def test_dashboard_shows_agent_cards(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        # Anomal runs moltbook plugin in mock — agent card should show plugin name
        assert "Moltbook" in resp.text
        assert "Anomal" in resp.text
        assert "running" in resp.text

    @pytest.mark.asyncio
    async def test_dashboard_shows_system_health(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert "Supervisor" in resp.text
        assert "Identities" in resp.text
        assert "LLM Calls" in resp.text
        assert "Error Rate" in resp.text


class TestDashboardPartials:
    @pytest.mark.asyncio
    async def test_agent_status_partial(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/partials/agent-status",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Anomal" in resp.text
        assert "running" in resp.text

    @pytest.mark.asyncio
    async def test_system_health_partial(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/partials/system-health",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Supervisor" in resp.text
        assert "LLM Calls" in resp.text

    @pytest.mark.asyncio
    async def test_audit_recent_partial(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/partials/audit-recent",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "api_call" in resp.text

    @pytest.mark.asyncio
    async def test_audit_recent_with_category_filter(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/partials/audit-recent?category=moltbook",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200


class TestAgentActions:
    @pytest.mark.asyncio
    async def test_start_agent(self, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/agent/anomal/start",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        assert "Anomal" in resp.text

    @pytest.mark.asyncio
    async def test_stop_agent(self, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/agent/anomal/stop",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        assert "Anomal" in resp.text

    @pytest.mark.asyncio
    async def test_start_agent_supervisor_offline(self, app, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        # Simulate supervisor offline
        app.state.supervisor_service.start_agent.return_value = {
            "success": False, "error": "Supervisor not reachable",
        }
        app.state.supervisor_service.get_status.return_value = None
        app.state.supervisor_service.get_agents.return_value = []

        resp = await client.post(
            "/agent/anomal/start",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        # Should still render the partial (with offline state)
        assert "offline" in resp.text.lower() or "Supervisor" in resp.text

    @pytest.mark.asyncio
    async def test_stop_agent_supervisor_offline(self, app, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        app.state.supervisor_service.stop_agent.return_value = {
            "success": False, "error": "Supervisor not reachable",
        }
        app.state.supervisor_service.get_status.return_value = None
        app.state.supervisor_service.get_agents.return_value = []

        resp = await client.post(
            "/agent/anomal/stop",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_start_agent_rejects_bad_csrf(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.post(
            "/agent/anomal/start",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": "invalid-token"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_dashboard_shows_stopped_agents(self, app, client, session_cookie):
        """Dashboard shows all configured agents, including stopped ones."""
        cookie_value, _ = session_cookie
        # Anomal is running, Cherry is not (mock only has anomal in agents)
        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        # Cherry (telegram plugin) should show as offline
        assert "Cherry" in resp.text
        assert "Telegram" in resp.text
