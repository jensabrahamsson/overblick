"""Tests for onboarding wizard routes."""

import pytest
from overblick.dashboard.auth import SESSION_COOKIE


class TestOnboardingWizard:
    @pytest.mark.asyncio
    async def test_step1_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Name Your Identity" in resp.text

    @pytest.mark.asyncio
    async def test_step1_with_explicit_param(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=1", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Name Your Identity" in resp.text

    @pytest.mark.asyncio
    async def test_step2_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=2", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Choose a Personality" in resp.text

    @pytest.mark.asyncio
    async def test_step3_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=3", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "LLM Configuration" in resp.text

    @pytest.mark.asyncio
    async def test_step4_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=4", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Plugins" in resp.text

    @pytest.mark.asyncio
    async def test_step5_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=5", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Secrets" in resp.text

    @pytest.mark.asyncio
    async def test_step6_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=6", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Review" in resp.text

    @pytest.mark.asyncio
    async def test_step7_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=7", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_step1_submit(self, client, session_cookie):
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "1", "name": "testbot", "description": "A test bot", "display_name": "TestBot", "csrf_token": csrf},
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    @pytest.mark.asyncio
    async def test_step1_invalid_name(self, client, session_cookie):
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "1", "name": "INVALID!", "description": "", "display_name": "", "csrf_token": csrf},
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_step1_duplicate_name(self, client, session_cookie, mock_onboarding_service):
        mock_onboarding_service.identity_exists.return_value = True
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "1", "name": "anomal", "description": "", "display_name": "", "csrf_token": csrf},
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 400
        assert "already exists" in resp.text
