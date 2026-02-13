"""Tests for authentication — sessions, CSRF, login, rate limiting."""

import time
import pytest
from overblick.dashboard.auth import SessionManager, SESSION_COOKIE


class TestSessionManager:
    def setup_method(self):
        self.sm = SessionManager("test-secret-key-12345", max_age_hours=1)

    def test_create_session(self):
        cookie, csrf = self.sm.create_session()
        assert cookie
        assert csrf
        assert len(csrf) == 64  # hex(32 bytes)

    def test_validate_valid_session(self):
        cookie, csrf = self.sm.create_session()
        result = self.sm.validate_session(cookie)
        assert result is not None
        assert result["authenticated"] is True
        assert result["csrf_token"] == csrf

    def test_validate_invalid_session(self):
        result = self.sm.validate_session("garbage-cookie-data")
        assert result is None

    def test_validate_tampered_session(self):
        cookie, _ = self.sm.create_session()
        tampered = cookie[:-5] + "XXXXX"
        result = self.sm.validate_session(tampered)
        assert result is None

    def test_csrf_validation(self):
        cookie, csrf = self.sm.create_session()
        session_data = self.sm.validate_session(cookie)
        assert self.sm.validate_csrf(session_data, csrf) is True

    def test_csrf_validation_wrong_token(self):
        cookie, csrf = self.sm.create_session()
        session_data = self.sm.validate_session(cookie)
        assert self.sm.validate_csrf(session_data, "wrong-token") is False

    def test_csrf_validation_empty_token(self):
        cookie, csrf = self.sm.create_session()
        session_data = self.sm.validate_session(cookie)
        assert self.sm.validate_csrf(session_data, "") is False

    def test_different_sessions_different_csrf(self):
        _, csrf1 = self.sm.create_session()
        _, csrf2 = self.sm.create_session()
        assert csrf1 != csrf2


class TestLoginRoute:
    @pytest.mark.asyncio
    async def test_login_page_renders(self, client):
        resp = await client.get("/login")
        assert resp.status_code == 200
        assert "Dashboard Password" in resp.text

    @pytest.mark.asyncio
    async def test_login_success(self, client, config):
        resp = await client.post("/login", data={
            "password": config.password,
            "csrf_token": "",
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("location") == "/"
        assert SESSION_COOKIE in resp.cookies

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client):
        resp = await client.post("/login", data={
            "password": "wrong",
            "csrf_token": "",
        })
        assert resp.status_code == 401
        assert "Invalid password" in resp.text

    @pytest.mark.asyncio
    async def test_login_rate_limiting(self, client):
        # Exhaust rate limit (5 attempts)
        for _ in range(5):
            await client.post("/login", data={"password": "wrong", "csrf_token": ""})

        # 6th attempt should be rate limited
        resp = await client.post("/login", data={"password": "wrong", "csrf_token": ""})
        assert resp.status_code == 429
        assert "Too many login attempts" in resp.text

    @pytest.mark.asyncio
    async def test_logout(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/logout",
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers.get("location") == "/login"

    @pytest.mark.asyncio
    async def test_unauthenticated_redirect(self, client):
        resp = await client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("location") == "/login"

    @pytest.mark.asyncio
    async def test_authenticated_access(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200


class TestAutoLogin:
    @pytest.mark.asyncio
    async def test_auto_login_no_password(self, config_no_password, mock_identity_service,
                                           mock_personality_service, mock_audit_service,
                                           mock_supervisor_service, mock_system_service,
                                           mock_onboarding_service):
        """When no password is set, /login auto-redirects with session."""
        from overblick.dashboard.app import create_app
        from httpx import AsyncClient, ASGITransport

        from overblick.dashboard.app import _create_templates
        from overblick.dashboard.security import RateLimiter

        app = create_app(config_no_password)
        app.state.session_manager = SessionManager(
            config_no_password.secret_key, config_no_password.session_hours,
        )
        app.state.rate_limiter = RateLimiter()
        app.state.templates = _create_templates()
        app.state.identity_service = mock_identity_service
        app.state.personality_service = mock_personality_service
        app.state.audit_service = mock_audit_service
        app.state.supervisor_service = mock_supervisor_service
        app.state.system_service = mock_system_service
        app.state.onboarding_service = mock_onboarding_service

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/login", follow_redirects=False)
            assert resp.status_code == 302
            assert SESSION_COOKIE in resp.cookies
