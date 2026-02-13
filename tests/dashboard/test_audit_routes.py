"""Tests for audit trail routes."""

import pytest
from overblick.dashboard.auth import SESSION_COOKIE


class TestAuditPage:
    @pytest.mark.asyncio
    async def test_audit_page_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/audit", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Audit Trail" in resp.text

    @pytest.mark.asyncio
    async def test_audit_page_shows_entries(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/audit", cookies={SESSION_COOKIE: cookie_value})
        assert "api_call" in resp.text
        assert "moltbook" in resp.text

    @pytest.mark.asyncio
    async def test_audit_page_shows_filters(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/audit", cookies={SESSION_COOKIE: cookie_value})
        assert "All Identities" in resp.text
        assert "All Categories" in resp.text

    @pytest.mark.asyncio
    async def test_audit_filtered_partial(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/partials/audit-filtered?identity=anomal&hours=24",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
