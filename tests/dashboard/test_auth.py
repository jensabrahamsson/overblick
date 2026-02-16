"""
Comprehensive tests for dashboard authentication and CSRF system.

Covers:
- SessionManager: create, validate, expiry, CSRF
- CSRF token generation, validation, tampering, timing-safe comparison
- AuthMiddleware: redirects, public paths, CSRF on mutating methods, htmx 401
- RateLimiter: limits, window reset, key isolation, clear
- Form validation models (LoginForm, OnboardingNameForm, etc.)
"""

import hmac
import time

import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, MagicMock, patch

from overblick.dashboard.auth import (
    AuthMiddleware,
    SessionManager,
    SESSION_COOKIE,
    CSRF_COOKIE,
    LOGIN_CSRF_COOKIE,
    PUBLIC_PATHS,
    get_session,
    check_csrf,
)
from overblick.dashboard.security import (
    RateLimiter,
    LoginForm,
    OnboardingNameForm,
    OnboardingLLMForm,
    OnboardingSecretsForm,
    AuditFilterForm,
)


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class TestSessionManager:
    """Core session management — creation, validation, expiry."""

    def setup_method(self):
        self.sm = SessionManager("test-secret-key-12345", max_age_hours=1)

    # -- Creation --

    def test_create_session_returns_cookie_and_csrf(self):
        cookie, csrf = self.sm.create_session()
        assert isinstance(cookie, str) and len(cookie) > 0
        assert isinstance(csrf, str) and len(csrf) == 64  # hex(32 bytes)

    def test_create_session_unique_csrf_tokens(self):
        """Every session must have a unique CSRF token."""
        tokens = {self.sm.create_session()[1] for _ in range(50)}
        assert len(tokens) == 50

    def test_create_session_unique_cookies(self):
        """Every session cookie value is unique."""
        cookies = {self.sm.create_session()[0] for _ in range(20)}
        assert len(cookies) == 20

    # -- Validation --

    def test_validate_session_success(self):
        cookie, csrf = self.sm.create_session()
        payload = self.sm.validate_session(cookie)
        assert payload is not None
        assert payload["authenticated"] is True
        assert payload["csrf_token"] == csrf
        assert "created_at" in payload

    def test_validate_session_invalid_garbage(self):
        assert self.sm.validate_session("garbage-cookie-data") is None

    def test_validate_session_empty_string(self):
        assert self.sm.validate_session("") is None

    def test_validate_session_tampered_cookie(self):
        cookie, _ = self.sm.create_session()
        tampered = cookie[:-5] + "XXXXX"
        assert self.sm.validate_session(tampered) is None

    def test_validate_session_wrong_secret_key(self):
        """A cookie signed with a different key must not validate."""
        other_sm = SessionManager("different-secret-key-99999", max_age_hours=1)
        cookie, _ = other_sm.create_session()
        assert self.sm.validate_session(cookie) is None

    # -- Expiry --

    def test_session_not_expired_within_window(self):
        cookie, _ = self.sm.create_session()
        # Immediately after creation it should be valid
        assert self.sm.validate_session(cookie) is not None

    def test_session_expired_after_max_age(self):
        """Session must be rejected after max_age has passed."""
        sm = SessionManager("test-key", max_age_hours=1)
        cookie, _ = sm.create_session()

        # Fast-forward time past expiry (1 hour + 1 second)
        with patch("itsdangerous.TimestampSigner.get_timestamp") as mock_ts:
            # itsdangerous uses int(time.time()) internally
            mock_ts.return_value = int(time.time()) + 3601
            assert sm.validate_session(cookie) is None

    def test_session_valid_just_before_expiry(self):
        """Session created moments ago must still be valid (well within max_age)."""
        sm = SessionManager("test-key", max_age_hours=1)
        cookie, _ = sm.create_session()
        # Created just now, validated immediately — well within the 1-hour window
        assert sm.validate_session(cookie) is not None


# ---------------------------------------------------------------------------
# CSRF Token Validation
# ---------------------------------------------------------------------------


class TestCSRFValidation:
    """CSRF token generation, validation, and security properties."""

    def setup_method(self):
        self.sm = SessionManager("csrf-test-key-67890", max_age_hours=1)

    def test_csrf_matches_session(self):
        cookie, csrf = self.sm.create_session()
        session = self.sm.validate_session(cookie)
        assert self.sm.validate_csrf(session, csrf) is True

    def test_csrf_wrong_token(self):
        cookie, _ = self.sm.create_session()
        session = self.sm.validate_session(cookie)
        assert self.sm.validate_csrf(session, "definitely-wrong-token") is False

    def test_csrf_empty_token(self):
        cookie, _ = self.sm.create_session()
        session = self.sm.validate_session(cookie)
        assert self.sm.validate_csrf(session, "") is False

    def test_csrf_none_token_not_accepted(self):
        """Passing None as a token should not pass validation."""
        cookie, _ = self.sm.create_session()
        session = self.sm.validate_session(cookie)
        # validate_csrf expects a str, but callers could pass None accidentally
        assert self.sm.validate_csrf(session, None) is False

    def test_csrf_cross_session_rejection(self):
        """CSRF token from session A must not validate against session B."""
        _, csrf_a = self.sm.create_session()
        cookie_b, _ = self.sm.create_session()
        session_b = self.sm.validate_session(cookie_b)
        assert self.sm.validate_csrf(session_b, csrf_a) is False

    def test_csrf_token_from_empty_session_data(self):
        """Empty session data should fail CSRF validation."""
        assert self.sm.validate_csrf({}, "some-token") is False

    def test_csrf_token_with_missing_key(self):
        """Session data without csrf_token key should fail."""
        assert self.sm.validate_csrf({"authenticated": True}, "some-token") is False

    def test_csrf_uses_timing_safe_comparison(self):
        """
        Verify that validate_csrf uses hmac.compare_digest for
        timing-safe comparison (prevents timing attacks).
        """
        cookie, csrf = self.sm.create_session()
        session = self.sm.validate_session(cookie)

        with patch("overblick.dashboard.auth.hmac.compare_digest", wraps=hmac.compare_digest) as mock_cmp:
            self.sm.validate_csrf(session, csrf)
            mock_cmp.assert_called_once_with(csrf, csrf)

    def test_csrf_timing_safe_comparison_with_wrong_token(self):
        """Timing-safe comparison is used even when token is wrong."""
        cookie, csrf = self.sm.create_session()
        session = self.sm.validate_session(cookie)
        wrong = "wrong-token"

        with patch("overblick.dashboard.auth.hmac.compare_digest", wraps=hmac.compare_digest) as mock_cmp:
            result = self.sm.validate_csrf(session, wrong)
            mock_cmp.assert_called_once_with(csrf, wrong)
            assert result is False


# ---------------------------------------------------------------------------
# AuthMiddleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    """Middleware enforcement of auth, CSRF, and htmx handling."""

    @pytest.mark.asyncio
    async def test_unauthenticated_redirect_to_login(self, client):
        """Unauthenticated GET to protected route redirects to /login."""
        resp = await client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("location") == "/login"

    @pytest.mark.asyncio
    async def test_unauthenticated_htmx_gets_401_with_redirect_header(self, client):
        """htmx requests get 401 with HX-Redirect header, not a 302 redirect."""
        resp = await client.get(
            "/",
            headers={"HX-Request": "true"},
            follow_redirects=False,
        )
        assert resp.status_code == 401
        assert resp.headers.get("HX-Redirect") == "/login"

    @pytest.mark.asyncio
    async def test_public_path_login_accessible(self, client):
        """The /login path is public and accessible without auth."""
        resp = await client.get("/login")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_public_path_health_accessible(self, client):
        """/health is a public path, accessible without auth."""
        resp = await client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_public_path_static_accessible(self, client):
        """/static/* paths are public (startswith matching)."""
        # This may 404 if no static files exist, but should not 302 redirect
        resp = await client.get("/static/nonexistent.js", follow_redirects=False)
        # Not a redirect — middleware let it through
        assert resp.status_code != 302

    @pytest.mark.asyncio
    async def test_authenticated_access_allowed(self, client, session_cookie):
        """Authenticated users can access protected routes."""
        cookie_value, _ = session_cookie
        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_csrf_on_post_with_valid_token(self, client, session_cookie, config):
        """POST /login with valid double-submit CSRF cookie passes CSRF check."""
        cookie_value, csrf_token = session_cookie
        # First GET /login to obtain the login CSRF cookie
        get_resp = await client.get("/login")
        login_csrf_cookie = get_resp.cookies.get(LOGIN_CSRF_COOKIE, "")
        assert login_csrf_cookie, "GET /login should set a login CSRF cookie"
        # POST with matching CSRF token in both cookie and form field
        resp = await client.post(
            "/login",
            data={"password": config.password, "csrf_token": login_csrf_cookie},
            cookies={SESSION_COOKIE: cookie_value, LOGIN_CSRF_COOKIE: login_csrf_cookie},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )
        # Login should succeed (302 redirect), not 403
        assert resp.status_code != 403

    @pytest.mark.asyncio
    async def test_csrf_on_post_with_invalid_token_rejected(self, client, session_cookie):
        """POST with wrong X-CSRF-Token header is rejected with 403."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/partials/audit-recent",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": "totally-wrong-csrf-token"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_csrf_on_put_with_invalid_token_rejected(self, client, session_cookie):
        """PUT with wrong X-CSRF-Token header is rejected with 403."""
        cookie_value, _ = session_cookie
        resp = await client.put(
            "/some-endpoint",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": "bogus-csrf"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_csrf_on_delete_with_invalid_token_rejected(self, client, session_cookie):
        """DELETE with wrong X-CSRF-Token header is rejected with 403."""
        cookie_value, _ = session_cookie
        resp = await client.delete(
            "/some-endpoint",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": "bogus-csrf"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_csrf_not_checked_on_get(self, client, session_cookie):
        """GET requests should not trigger CSRF validation."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/",
            cookies={SESSION_COOKIE: cookie_value},
            # No CSRF header, and it's fine for GET
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_post_without_csrf_header_blocked(self, client, session_cookie):
        """
        POST without X-CSRF-Token header to protected route is blocked with 403.
        """
        cookie_value, _ = session_cookie
        resp = await client.post(
            "/agent/anomal/start",
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_post_to_public_path_without_csrf_allowed(self, client, session_cookie, config):
        """
        POST to public path (/login) does not require middleware CSRF header.
        The login route has its own double-submit cookie CSRF instead.
        """
        cookie_value, _ = session_cookie
        # GET /login to obtain the login CSRF cookie
        get_resp = await client.get("/login")
        login_csrf = get_resp.cookies.get(LOGIN_CSRF_COOKIE, "")
        # POST with double-submit cookie CSRF (no X-CSRF-Token header needed)
        resp = await client.post(
            "/login",
            data={"password": config.password, "csrf_token": login_csrf},
            cookies={SESSION_COOKIE: cookie_value, LOGIN_CSRF_COOKIE: login_csrf},
            follow_redirects=False,
        )
        # Public path — middleware CSRF not checked, login CSRF passes
        assert resp.status_code != 403

    @pytest.mark.asyncio
    async def test_session_data_attached_to_request_state(self, client, session_cookie):
        """Middleware attaches session data to request.state for route handlers."""
        cookie_value, csrf = session_cookie
        # Access a protected route; if session data wasn't attached,
        # route handlers would fail. A 200 proves it was attached.
        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Helper functions: get_session, check_csrf
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    """Tests for module-level helper functions."""

    def test_get_session_returns_none_without_cookie(self):
        """get_session returns None when no session cookie is present."""
        request = MagicMock()
        request.cookies = {}
        app_state = MagicMock()
        app_state.session_manager = SessionManager("key", max_age_hours=1)
        request.app.state = app_state
        assert get_session(request) is None

    def test_get_session_returns_payload_with_valid_cookie(self):
        sm = SessionManager("helper-test-key", max_age_hours=1)
        cookie, csrf = sm.create_session()

        request = MagicMock()
        request.cookies = {SESSION_COOKIE: cookie}
        request.app.state.session_manager = sm

        result = get_session(request)
        assert result is not None
        assert result["csrf_token"] == csrf

    def test_get_session_returns_none_with_invalid_cookie(self):
        sm = SessionManager("helper-test-key", max_age_hours=1)

        request = MagicMock()
        request.cookies = {SESSION_COOKIE: "invalid-cookie-value"}
        request.app.state.session_manager = sm

        assert get_session(request) is None

    def test_check_csrf_with_header_token(self):
        sm = SessionManager("csrf-helper-key", max_age_hours=1)
        cookie, csrf = sm.create_session()
        session = sm.validate_session(cookie)

        request = MagicMock()
        request.headers = {"X-CSRF-Token": csrf}
        request.app.state.session_manager = sm

        assert check_csrf(request, session) is True

    def test_check_csrf_with_wrong_header_token(self):
        sm = SessionManager("csrf-helper-key", max_age_hours=1)
        cookie, csrf = sm.create_session()
        session = sm.validate_session(cookie)

        request = MagicMock()
        request.headers = {"X-CSRF-Token": "wrong-token"}
        request.app.state.session_manager = sm

        assert check_csrf(request, session) is False

    def test_check_csrf_rejects_without_header(self):
        """When no X-CSRF-Token header, check_csrf returns False."""
        sm = SessionManager("csrf-helper-key", max_age_hours=1)
        cookie, csrf = sm.create_session()
        session = sm.validate_session(cookie)

        request = MagicMock()
        request.headers = {}
        request.app.state.session_manager = sm

        assert check_csrf(request, session) is False


# ---------------------------------------------------------------------------
# Login and Logout Routes
# ---------------------------------------------------------------------------


class TestLoginRoute:
    """Login page rendering, success, failure, rate limiting."""

    async def _get_login_csrf(self, client):
        """GET /login to obtain the double-submit CSRF cookie and token."""
        resp = await client.get("/login")
        csrf_token = resp.cookies.get(LOGIN_CSRF_COOKIE, "")
        return csrf_token

    async def _post_login(self, client, password, csrf_token=None):
        """POST /login with proper double-submit CSRF cookie."""
        if csrf_token is None:
            csrf_token = await self._get_login_csrf(client)
        return await client.post(
            "/login",
            data={"password": password, "csrf_token": csrf_token},
            cookies={LOGIN_CSRF_COOKIE: csrf_token},
            follow_redirects=False,
        )

    @pytest.mark.asyncio
    async def test_login_page_renders(self, client):
        resp = await client.get("/login")
        assert resp.status_code == 200
        assert "Dashboard Password" in resp.text

    @pytest.mark.asyncio
    async def test_login_success(self, client, config):
        resp = await self._post_login(client, config.password)
        assert resp.status_code == 302
        assert resp.headers.get("location") == "/"
        assert SESSION_COOKIE in resp.cookies

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client):
        resp = await self._post_login(client, "wrong")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_rate_limiting(self, client):
        """After 5 failed attempts, the 6th is rate limited."""
        for _ in range(5):
            await self._post_login(client, "wrong")

        resp = await self._post_login(client, "wrong")
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


# ---------------------------------------------------------------------------
# Auto-Login (no password configured)
# ---------------------------------------------------------------------------


class TestAutoLogin:
    """When no password is set, dashboard auto-logs in."""

    @pytest.mark.asyncio
    async def test_auto_login_no_password(
        self, config_no_password,
        mock_identity_service, mock_personality_service,
        mock_audit_service, mock_supervisor_service,
        mock_system_service, mock_onboarding_service,
    ):
        """When no password is set, /login auto-redirects with session."""
        from overblick.dashboard.app import create_app, _create_templates
        from httpx import AsyncClient, ASGITransport

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

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get("/login", follow_redirects=False)
            assert resp.status_code == 302
            assert SESSION_COOKIE in resp.cookies


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    """In-memory sliding window rate limiter."""

    def test_allows_within_limit(self):
        rl = RateLimiter()
        for i in range(5):
            assert rl.check("key", max_requests=5, window_seconds=60) is True

    def test_blocks_over_limit(self):
        rl = RateLimiter()
        for _ in range(5):
            rl.check("key", max_requests=5, window_seconds=60)
        assert rl.check("key", max_requests=5, window_seconds=60) is False

    def test_blocks_exactly_at_limit(self):
        """The Nth+1 request should be blocked (0-indexed check)."""
        rl = RateLimiter()
        results = [rl.check("key", max_requests=3, window_seconds=60) for _ in range(4)]
        assert results == [True, True, True, False]

    def test_different_keys_independent(self):
        """Rate limits are isolated by key."""
        rl = RateLimiter()
        for _ in range(5):
            rl.check("key_a", max_requests=5, window_seconds=60)
        assert rl.check("key_a", max_requests=5, window_seconds=60) is False
        assert rl.check("key_b", max_requests=5, window_seconds=60) is True

    def test_key_isolation_multiple_keys(self):
        """Multiple keys can each exhaust their own limits independently."""
        rl = RateLimiter()
        for _ in range(3):
            rl.check("alpha", max_requests=3, window_seconds=60)
            rl.check("beta", max_requests=3, window_seconds=60)

        assert rl.check("alpha", max_requests=3, window_seconds=60) is False
        assert rl.check("beta", max_requests=3, window_seconds=60) is False
        # A new key is unaffected
        assert rl.check("gamma", max_requests=3, window_seconds=60) is True

    def test_window_reset_after_expiry(self):
        """Requests outside the window should be purged, allowing new ones."""
        rl = RateLimiter()
        base_time = 1700000000.0

        with patch("overblick.dashboard.security.time.time") as mock_time:
            mock_time.return_value = base_time
            for _ in range(5):
                rl.check("key", max_requests=5, window_seconds=10)
            assert rl.check("key", max_requests=5, window_seconds=10) is False

            # Advance past window
            mock_time.return_value = base_time + 11.0
            assert rl.check("key", max_requests=5, window_seconds=10) is True

    def test_partial_window_expiry(self):
        """Only old entries should be purged; recent ones remain."""
        rl = RateLimiter()
        base_time = 1700000000.0

        with patch("overblick.dashboard.security.time.time") as mock_time:
            # Add 3 requests at t=0
            mock_time.return_value = base_time
            for _ in range(3):
                rl.check("key", max_requests=5, window_seconds=10)

            # Add 2 requests at t=6
            mock_time.return_value = base_time + 6.0
            for _ in range(2):
                rl.check("key", max_requests=5, window_seconds=10)

            # At t=11 the first 3 expire, but the 2 from t=6 remain
            mock_time.return_value = base_time + 11.0
            assert rl.check("key", max_requests=5, window_seconds=10) is True
            assert rl.check("key", max_requests=5, window_seconds=10) is True
            assert rl.check("key", max_requests=5, window_seconds=10) is True
            # Now we have 2 (from t=6) + 3 new = 5 total, next should fail
            assert rl.check("key", max_requests=5, window_seconds=10) is False

    def test_reset_clears_single_key(self):
        rl = RateLimiter()
        for _ in range(5):
            rl.check("key", max_requests=5, window_seconds=60)
        assert rl.check("key", max_requests=5, window_seconds=60) is False
        rl.reset("key")
        assert rl.check("key", max_requests=5, window_seconds=60) is True

    def test_reset_nonexistent_key_is_safe(self):
        """Resetting a key that doesn't exist should not raise."""
        rl = RateLimiter()
        rl.reset("nonexistent")  # Should not raise

    def test_clear_all_keys(self):
        rl = RateLimiter()
        rl.check("key1", max_requests=1, window_seconds=60)
        rl.check("key2", max_requests=1, window_seconds=60)
        rl.clear()
        assert rl.check("key1", max_requests=1, window_seconds=60) is True
        assert rl.check("key2", max_requests=1, window_seconds=60) is True

    def test_single_request_limit(self):
        """Edge case: max_requests=1 allows exactly one request."""
        rl = RateLimiter()
        assert rl.check("key", max_requests=1, window_seconds=60) is True
        assert rl.check("key", max_requests=1, window_seconds=60) is False


# ---------------------------------------------------------------------------
# Form Validation Models
# ---------------------------------------------------------------------------


class TestLoginForm:
    """LoginForm validation."""

    def test_valid(self):
        form = LoginForm(password="test123", csrf_token="abc123")
        assert form.password == "test123"
        assert form.csrf_token == "abc123"

    def test_empty_password_rejected(self):
        with pytest.raises(ValidationError):
            LoginForm(password="", csrf_token="abc")

    def test_empty_csrf_rejected(self):
        with pytest.raises(ValidationError):
            LoginForm(password="test", csrf_token="")

    def test_password_too_long(self):
        with pytest.raises(ValidationError):
            LoginForm(password="x" * 257, csrf_token="abc")

    def test_csrf_too_long(self):
        with pytest.raises(ValidationError):
            LoginForm(password="test", csrf_token="x" * 129)

    def test_missing_password_field(self):
        with pytest.raises(ValidationError):
            LoginForm(csrf_token="abc")

    def test_missing_csrf_field(self):
        with pytest.raises(ValidationError):
            LoginForm(password="test")


class TestOnboardingNameForm:
    """OnboardingNameForm validation — identity naming rules."""

    def test_valid_simple_name(self):
        form = OnboardingNameForm(name="myagent")
        assert form.name == "myagent"

    def test_valid_with_underscores(self):
        form = OnboardingNameForm(name="my_agent_v2")
        assert form.name == "my_agent_v2"

    def test_valid_with_numbers(self):
        form = OnboardingNameForm(name="agent42")
        assert form.name == "agent42"

    def test_valid_with_description(self):
        form = OnboardingNameForm(name="testbot", description="A test agent")
        assert form.description == "A test agent"

    def test_valid_with_display_name(self):
        form = OnboardingNameForm(name="testbot", display_name="Test Bot")
        assert form.display_name == "Test Bot"

    def test_lowercase_enforced_by_pattern(self):
        """Pattern rejects uppercase — names must be provided lowercase."""
        with pytest.raises(ValidationError):
            OnboardingNameForm(name="MyAgent")

    def test_starts_with_number_rejected(self):
        with pytest.raises(ValidationError):
            OnboardingNameForm(name="123agent")

    def test_special_characters_rejected(self):
        with pytest.raises(ValidationError):
            OnboardingNameForm(name="my agent!")

    def test_hyphen_rejected(self):
        """Pattern requires [a-z][a-z0-9_]* — hyphens not allowed."""
        with pytest.raises(ValidationError):
            OnboardingNameForm(name="my-agent")

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            OnboardingNameForm(name="")

    def test_name_too_long(self):
        with pytest.raises(ValidationError):
            OnboardingNameForm(name="a" * 65)

    def test_description_too_long(self):
        with pytest.raises(ValidationError):
            OnboardingNameForm(name="agent", description="x" * 501)

    def test_display_name_too_long(self):
        with pytest.raises(ValidationError):
            OnboardingNameForm(name="agent", display_name="x" * 101)


class TestOnboardingLLMForm:
    """OnboardingLLMForm validation — LLM configuration bounds."""

    def test_valid_defaults(self):
        form = OnboardingLLMForm()
        assert form.model == "qwen3:8b"
        assert form.temperature == 0.7
        assert form.max_tokens == 2000
        assert form.provider == "ollama"

    def test_valid_custom_values(self):
        form = OnboardingLLMForm(
            model="llama3:70b", temperature=1.5, max_tokens=4000, provider="gateway",
        )
        assert form.model == "llama3:70b"
        assert form.temperature == 1.5
        assert form.max_tokens == 4000
        assert form.provider == "gateway"

    def test_provider_cloud(self):
        form = OnboardingLLMForm(
            provider="cloud",
            cloud_api_url="https://api.openai.com/v1",
            cloud_model="gpt-4o",
        )
        assert form.provider == "cloud"
        assert form.cloud_api_url == "https://api.openai.com/v1"
        assert form.cloud_model == "gpt-4o"

    def test_provider_invalid(self):
        with pytest.raises(ValidationError):
            OnboardingLLMForm(provider="invalid")

    def test_temperature_lower_bound(self):
        form = OnboardingLLMForm(temperature=0.0)
        assert form.temperature == 0.0

    def test_temperature_upper_bound(self):
        form = OnboardingLLMForm(temperature=2.0)
        assert form.temperature == 2.0

    def test_temperature_below_lower_bound(self):
        with pytest.raises(ValidationError):
            OnboardingLLMForm(temperature=-0.1)

    def test_temperature_above_upper_bound(self):
        with pytest.raises(ValidationError):
            OnboardingLLMForm(temperature=2.1)

    def test_max_tokens_lower_bound(self):
        form = OnboardingLLMForm(max_tokens=100)
        assert form.max_tokens == 100

    def test_max_tokens_upper_bound(self):
        form = OnboardingLLMForm(max_tokens=8000)
        assert form.max_tokens == 8000

    def test_max_tokens_below_lower_bound(self):
        with pytest.raises(ValidationError):
            OnboardingLLMForm(max_tokens=99)

    def test_max_tokens_above_upper_bound(self):
        with pytest.raises(ValidationError):
            OnboardingLLMForm(max_tokens=8001)

    def test_empty_model_rejected(self):
        with pytest.raises(ValidationError):
            OnboardingLLMForm(model="")

    def test_model_too_long(self):
        with pytest.raises(ValidationError):
            OnboardingLLMForm(model="x" * 101)


class TestOnboardingSecretsForm:
    """OnboardingSecretsForm validation — secret key-value pairs."""

    def test_valid_empty(self):
        form = OnboardingSecretsForm()
        assert form.keys == []
        assert form.values == []

    def test_valid_keys(self):
        form = OnboardingSecretsForm(
            keys=["api_key", "bot-token", "secret123"],
            values=["val1", "val2", "val3"],
        )
        assert len(form.keys) == 3

    def test_valid_key_with_underscores(self):
        form = OnboardingSecretsForm(keys=["my_secret_key"], values=["val"])
        assert form.keys == ["my_secret_key"]

    def test_valid_key_with_hyphens(self):
        form = OnboardingSecretsForm(keys=["my-secret-key"], values=["val"])
        assert form.keys == ["my-secret-key"]

    def test_invalid_key_with_spaces(self):
        with pytest.raises(ValidationError):
            OnboardingSecretsForm(keys=["my secret"], values=["val"])

    def test_invalid_key_with_special_chars(self):
        with pytest.raises(ValidationError):
            OnboardingSecretsForm(keys=["my@key!"], values=["val"])

    def test_invalid_key_with_dots(self):
        with pytest.raises(ValidationError):
            OnboardingSecretsForm(keys=["my.key"], values=["val"])


class TestAuditFilterForm:
    """AuditFilterForm validation — audit query parameters."""

    def test_defaults(self):
        form = AuditFilterForm()
        assert form.identity == ""
        assert form.category == ""
        assert form.action == ""
        assert form.hours == 24
        assert form.limit == 50

    def test_custom_values(self):
        form = AuditFilterForm(
            identity="anomal", category="security", action="llm_request",
            hours=48, limit=100,
        )
        assert form.identity == "anomal"
        assert form.hours == 48
        assert form.limit == 100

    def test_hours_lower_bound(self):
        form = AuditFilterForm(hours=1)
        assert form.hours == 1

    def test_hours_upper_bound(self):
        form = AuditFilterForm(hours=720)
        assert form.hours == 720

    def test_hours_below_lower_bound(self):
        with pytest.raises(ValidationError):
            AuditFilterForm(hours=0)

    def test_hours_above_upper_bound(self):
        with pytest.raises(ValidationError):
            AuditFilterForm(hours=721)

    def test_limit_lower_bound(self):
        form = AuditFilterForm(limit=1)
        assert form.limit == 1

    def test_limit_upper_bound(self):
        form = AuditFilterForm(limit=500)
        assert form.limit == 500

    def test_limit_below_lower_bound(self):
        with pytest.raises(ValidationError):
            AuditFilterForm(limit=0)

    def test_limit_above_upper_bound(self):
        with pytest.raises(ValidationError):
            AuditFilterForm(limit=501)

    def test_identity_too_long(self):
        with pytest.raises(ValidationError):
            AuditFilterForm(identity="x" * 65)

    def test_category_too_long(self):
        with pytest.raises(ValidationError):
            AuditFilterForm(category="x" * 65)

    def test_action_too_long(self):
        with pytest.raises(ValidationError):
            AuditFilterForm(action="x" * 65)


# ---------------------------------------------------------------------------
# Public paths constant
# ---------------------------------------------------------------------------


class TestPublicPaths:
    """Verify the PUBLIC_PATHS set is correctly defined."""

    def test_login_is_public(self):
        assert "/login" in PUBLIC_PATHS

    def test_static_is_public(self):
        assert "/static" in PUBLIC_PATHS

    def test_health_is_public(self):
        assert "/health" in PUBLIC_PATHS

    def test_root_is_not_public(self):
        assert "/" not in PUBLIC_PATHS
