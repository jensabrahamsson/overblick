"""Tests for agent detail routes."""

import pytest
from overblick.dashboard.auth import SESSION_COOKIE


class TestAgentDetail:
    @pytest.mark.asyncio
    async def test_agent_detail_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/agent/anomal",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Anomal" in resp.text
        assert "qwen3:8b" in resp.text

    @pytest.mark.asyncio
    async def test_agent_detail_not_found(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/agent/nonexistent",
            cookies={SESSION_COOKIE: cookie_value},
        )
        # Unknown agent now redirects to dashboard with an error message
        assert resp.status_code == 302
        assert "not+found" in resp.headers.get("location", "") or "/" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_agent_detail_shows_plugins(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/agent/anomal",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert "moltbook" in resp.text

    @pytest.mark.asyncio
    async def test_agent_detail_shows_capabilities(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/agent/anomal",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert "psychology" in resp.text
