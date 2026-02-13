"""Tests for identities page."""

import pytest
from overblick.dashboard.auth import SESSION_COOKIE


class TestIdentitiesPage:
    @pytest.mark.asyncio
    async def test_identities_page_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/identities", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Identities" in resp.text

    @pytest.mark.asyncio
    async def test_identities_page_shows_all(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/identities", cookies={SESSION_COOKIE: cookie_value})
        assert "Anomal" in resp.text
        assert "Cherry" in resp.text

    @pytest.mark.asyncio
    async def test_identities_page_has_create_button(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/identities", cookies={SESSION_COOKIE: cookie_value})
        assert "Create New Identity" in resp.text
