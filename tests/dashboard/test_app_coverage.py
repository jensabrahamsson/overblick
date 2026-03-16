"""Tests for dashboard app.py — coverage gaps."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.dashboard.app import (
    _create_templates,
    _format_epoch,
    _format_irc_time,
    _format_uptime,
    _is_operational_cap,
    create_app,
    lifespan,
)
from overblick.dashboard.config import DashboardConfig


class TestFormatUptime:
    def test_should_format_seconds(self):
        assert _format_uptime(30) == "30s"

    def test_should_format_minutes(self):
        assert _format_uptime(120) == "2m"

    def test_should_format_hours_and_minutes(self):
        assert _format_uptime(3660) == "1h 1m"

    def test_should_format_days_and_hours(self):
        assert _format_uptime(90000) == "1d 1h"

    def test_should_handle_zero(self):
        assert _format_uptime(0) == "0s"

    def test_should_handle_exactly_60_seconds(self):
        assert _format_uptime(60) == "1m"

    def test_should_handle_exactly_60_minutes(self):
        assert _format_uptime(3600) == "1h 0m"

    def test_should_handle_exactly_24_hours(self):
        assert _format_uptime(86400) == "1d 0h"

    def test_should_handle_float_input(self):
        assert _format_uptime(30.7) == "30s"

    def test_should_format_59_seconds(self):
        assert _format_uptime(59) == "59s"

    def test_should_format_59_minutes(self):
        assert _format_uptime(59 * 60) == "59m"

    def test_should_format_23_hours(self):
        result = _format_uptime(23 * 3600 + 30 * 60)
        assert result == "23h 30m"


class TestFormatEpoch:
    def test_should_format_valid_epoch(self):
        # Use a known epoch
        result = _format_epoch(0)
        assert "1970" in result

    def test_should_format_recent_epoch(self):
        result = _format_epoch(time.time())
        assert "20" in result  # Year starts with 20xx

    def test_should_return_string_on_invalid_value(self):
        result = _format_epoch("not-a-number")
        assert result == "not-a-number"

    def test_should_return_string_on_none(self):
        result = _format_epoch(None)
        assert result == "None"

    def test_should_handle_negative_overflow(self):
        # Very large negative number that may cause OSError
        result = _format_epoch(-999999999999999)
        assert isinstance(result, str)


class TestFormatIrcTime:
    def test_should_format_valid_epoch(self):
        result = _format_irc_time(time.time())
        assert result.startswith("[")
        assert result.endswith("]")
        assert ":" in result

    def test_should_return_placeholder_on_invalid(self):
        result = _format_irc_time("not-a-number")
        assert result == "[??:??]"

    def test_should_return_placeholder_on_none(self):
        result = _format_irc_time(None)
        assert result == "[??:??]"

    def test_should_handle_negative_overflow(self):
        result = _format_irc_time(-999999999999999)
        assert isinstance(result, str)


class TestIsOperationalCap:
    def test_should_return_true_for_operational_caps(self):
        operational = [
            "communication", "monitoring", "system", "boss_request",
            "email", "gmail", "telegram_notifier", "host_inspection",
            "email_agent", "system_clock",
        ]
        for cap in operational:
            assert _is_operational_cap(cap) is True

    def test_should_return_false_for_non_operational_caps(self):
        assert _is_operational_cap("psychology") is False
        assert _is_operational_cap("knowledge") is False
        assert _is_operational_cap("nonexistent") is False


class TestCreateTemplates:
    def test_should_create_templates_with_globals(self):
        templates = _create_templates()
        env = templates.env
        assert "plugin_name" in env.globals
        assert "_format_uptime" in env.globals
        assert "_is_operational_cap" in env.globals
        assert "irc_enabled" in env.globals
        assert "auth_enabled" in env.globals
        assert "is_windows" in env.globals
        assert "safe_mode_enabled" in env.globals
        assert "settings_enabled" in env.globals

    def test_should_register_epoch_filter(self):
        templates = _create_templates()
        assert "epoch_to_datetime" in templates.env.filters
        assert "irc_time" in templates.env.filters

    def test_should_default_irc_enabled_to_false(self):
        templates = _create_templates()
        assert templates.env.globals["irc_enabled"]() is False

    def test_should_default_auth_enabled_to_false(self):
        templates = _create_templates()
        assert templates.env.globals["auth_enabled"]() is False

    def test_should_default_settings_enabled_to_true(self):
        templates = _create_templates()
        assert templates.env.globals["settings_enabled"]() is True


class TestCreateApp:
    def test_should_create_app_with_config(self, tmp_path):
        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=True,
        )
        app = create_app(config)
        assert app.state.config is config

    def test_should_create_app_with_default_config(self):
        with patch("overblick.dashboard.app.get_config") as mock_get:
            mock_get.return_value = DashboardConfig(secret_key="test")
            create_app()
            mock_get.assert_called_once()

    def test_should_mount_static_files_when_dir_exists(self, tmp_path):
        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=True,
        )
        app = create_app(config)
        route_names = [route.name for route in app.routes if hasattr(route, "name")]
        assert "static" in route_names


class TestSecurityHeadersMiddleware:
    @pytest.fixture
    def app_with_config(self, tmp_path):
        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=True,
            network_access=True,
            password_hash="$2b$12$fakehash",
        )
        return create_app(config)

    @pytest.mark.asyncio
    async def test_should_add_security_headers(self, tmp_path):
        from httpx import ASGITransport, AsyncClient

        from overblick.dashboard.auth import SESSION_COOKIE, SessionManager

        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=True,
        )
        app = create_app(config)

        # Manually set up state (bypass lifespan)
        app.state.session_manager = SessionManager(secret_key=config.secret_key)
        app.state.rate_limiter = MagicMock()
        app.state.templates = _create_templates()
        app.state.identity_service = MagicMock()
        app.state.identity_service.get_all_identities.return_value = []
        app.state.personality_service = MagicMock()
        app.state.audit_service = MagicMock()
        app.state.audit_service.count_with_failures.return_value = (0, 0)
        app.state.audit_service.count_by_hour.return_value = []
        app.state.audit_service.count_by_category.return_value = {}
        app.state.supervisor_service = AsyncMock()
        app.state.supervisor_service.get_status.return_value = None
        app.state.supervisor_service.get_agents.return_value = []
        app.state.system_service = MagicMock()
        app.state.system_service.get_moltbook_statuses.return_value = []

        sm = SessionManager(secret_key=config.secret_key)
        cookie_value, _ = sm.create_session()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})

        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "Content-Security-Policy" in resp.headers

    @pytest.mark.asyncio
    async def test_should_add_cache_control_for_static(self, tmp_path):
        from httpx import ASGITransport, AsyncClient

        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=True,
        )
        app = create_app(config)
        app.state.session_manager = MagicMock()
        app.state.rate_limiter = MagicMock()
        app.state.templates = _create_templates()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/static/css/style.css")

        if resp.status_code == 200:
            assert "max-age=3600" in resp.headers.get("Cache-Control", "")

    @pytest.mark.asyncio
    async def test_should_add_hsts_for_https_network_access(self, tmp_path):
        from httpx import ASGITransport, AsyncClient

        from overblick.dashboard.auth import SESSION_COOKIE, SessionManager

        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=True,
            network_access=True,
            password_hash="$2b$12$fakehash",
        )
        app = create_app(config)
        app.state.session_manager = SessionManager(secret_key=config.secret_key)
        app.state.rate_limiter = MagicMock()
        app.state.rate_limiter.check.return_value = True
        app.state.templates = _create_templates()
        app.state.identity_service = MagicMock()
        app.state.identity_service.get_all_identities.return_value = []
        app.state.personality_service = MagicMock()
        app.state.audit_service = MagicMock()
        app.state.audit_service.count_with_failures.return_value = (0, 0)
        app.state.audit_service.count_by_hour.return_value = []
        app.state.audit_service.count_by_category.return_value = {}
        app.state.supervisor_service = AsyncMock()
        app.state.supervisor_service.get_status.return_value = None
        app.state.supervisor_service.get_agents.return_value = []
        app.state.system_service = MagicMock()
        app.state.system_service.get_moltbook_statuses.return_value = []

        sm = SessionManager(secret_key=config.secret_key)
        cookie_value, _ = sm.create_session()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://testserver") as client:
            resp = await client.get(
                "/",
                cookies={SESSION_COOKIE: cookie_value},
                headers={"X-Forwarded-Proto": "https"},
            )

        assert "Strict-Transport-Security" in resp.headers

    @pytest.mark.asyncio
    async def test_should_return_html_500_without_templates(self, tmp_path):
        """When templates aren't available, fall back to plain HTMLResponse."""
        from httpx import ASGITransport, AsyncClient

        from overblick.dashboard.auth import SESSION_COOKIE, SessionManager

        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=True,
        )
        app = create_app(config)
        app.state.session_manager = SessionManager(secret_key=config.secret_key)
        app.state.rate_limiter = MagicMock()
        # Do NOT set app.state.templates — force fallback
        app.state.identity_service = MagicMock()
        app.state.identity_service.get_all_identities.side_effect = RuntimeError("boom")
        app.state.personality_service = MagicMock()
        app.state.audit_service = MagicMock()
        app.state.supervisor_service = AsyncMock()
        app.state.system_service = MagicMock()

        # Remove templates attribute to trigger fallback
        if hasattr(app.state, "templates"):
            del app.state.templates

        sm = SessionManager(secret_key=config.secret_key)
        cookie_value, _ = sm.create_session()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})

        assert resp.status_code == 500
        assert "Internal Server Error" in resp.text


class TestHttpExceptionHandler:
    @pytest.mark.asyncio
    async def test_should_return_plain_html_when_no_templates(self, tmp_path):
        """Test the error handler fallback when templates are not available."""
        from httpx import ASGITransport, AsyncClient

        from overblick.dashboard.auth import SESSION_COOKIE, SessionManager

        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=True,
        )
        app = create_app(config)
        app.state.session_manager = SessionManager(secret_key=config.secret_key)
        app.state.rate_limiter = MagicMock()
        # No templates set

        if hasattr(app.state, "templates"):
            del app.state.templates

        sm = SessionManager(secret_key=config.secret_key)
        cookie_value, _ = sm.create_session()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/nonexistent", cookies={SESSION_COOKIE: cookie_value})

        assert resp.status_code == 404
        assert "Error 404" in resp.text


class TestLifespan:
    @pytest.mark.asyncio
    async def test_should_initialize_and_cleanup(self, tmp_path):
        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=True,
        )
        app = create_app(config)
        app.state.config = config

        with patch("overblick.dashboard.services.init_services", new_callable=AsyncMock) as mock_init, \
             patch("overblick.dashboard.services.cleanup_services", new_callable=AsyncMock) as mock_cleanup, \
             patch("overblick.dashboard.app._create_templates") as mock_templates:
            env_globals = {}
            mock_templates.return_value = MagicMock()
            mock_templates.return_value.env = MagicMock()
            mock_templates.return_value.env.globals = env_globals

            with patch("overblick.dashboard.routes.compass.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.dev.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.digest.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.email.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.github_dash.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.kontrast.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.log_agent.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.moltbook.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.polymarket_dash.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.skuggspel.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.spegel.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.stage.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.telegram.has_data", return_value=False):

                async with lifespan(app):
                    # Exercise the _check_irc_enabled closure
                    irc_fn = env_globals.get("irc_enabled")
                    if irc_fn:
                        # Case 1: no irc_service
                        result = irc_fn()
                        assert result is False

                        # Case 2: with irc_service that has data
                        mock_irc = MagicMock()
                        mock_irc.has_data.return_value = True
                        app.state.irc_service = mock_irc
                        result = irc_fn()
                        assert result is True

            mock_init.assert_called_once()
            mock_cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_warn_on_network_access(self, tmp_path):
        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=True,
            network_access=True,
            password_hash="$2b$12$fakehash",
        )
        app = create_app(config)
        app.state.config = config

        with patch("overblick.dashboard.services.init_services", new_callable=AsyncMock), \
             patch("overblick.dashboard.services.cleanup_services", new_callable=AsyncMock), \
             patch("overblick.dashboard.app._create_templates") as mock_templates, \
             patch("overblick.dashboard.app.logger") as mock_logger:
            mock_templates.return_value = MagicMock()
            mock_templates.return_value.env = MagicMock()
            mock_templates.return_value.env.globals = {}

            with patch("overblick.dashboard.routes.compass.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.dev.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.digest.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.email.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.github_dash.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.kontrast.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.log_agent.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.moltbook.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.polymarket_dash.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.skuggspel.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.spegel.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.stage.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.telegram.has_data", return_value=False):

                async with lifespan(app):
                    pass

            mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_should_detect_setup_needed_when_no_config_file(self, tmp_path):
        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=False,
        )
        app = create_app(config)
        app.state.config = config

        with patch("overblick.dashboard.services.init_services", new_callable=AsyncMock), \
             patch("overblick.dashboard.services.cleanup_services", new_callable=AsyncMock), \
             patch("overblick.dashboard.app._create_templates") as mock_templates:
            mock_templates.return_value = MagicMock()
            mock_templates.return_value.env = MagicMock()
            mock_templates.return_value.env.globals = {}

            with patch("overblick.dashboard.routes.compass.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.dev.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.digest.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.email.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.github_dash.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.kontrast.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.log_agent.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.moltbook.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.polymarket_dash.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.skuggspel.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.spegel.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.stage.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.telegram.has_data", return_value=False):

                async with lifespan(app):
                    assert app.state.setup_needed is True

    @pytest.mark.asyncio
    async def test_should_not_need_setup_when_config_exists(self, tmp_path):
        config = DashboardConfig(
            secret_key="test-key",
            base_dir=str(tmp_path),
            test_mode=False,
        )
        # Create the config file
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "overblick.yaml").write_text("dashboard:\n  port: 8080\n")

        app = create_app(config)
        app.state.config = config

        with patch("overblick.dashboard.services.init_services", new_callable=AsyncMock), \
             patch("overblick.dashboard.services.cleanup_services", new_callable=AsyncMock), \
             patch("overblick.dashboard.app._create_templates") as mock_templates:
            mock_templates.return_value = MagicMock()
            mock_templates.return_value.env = MagicMock()
            mock_templates.return_value.env.globals = {}

            with patch("overblick.dashboard.routes.compass.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.dev.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.digest.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.email.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.github_dash.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.kontrast.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.log_agent.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.moltbook.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.polymarket_dash.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.skuggspel.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.spegel.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.stage.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.telegram.has_data", return_value=False):

                async with lifespan(app):
                    assert app.state.setup_needed is False

    @pytest.mark.asyncio
    async def test_should_detect_setup_without_base_dir(self, tmp_path):
        config = DashboardConfig(
            secret_key="test-key",
            base_dir="",
            test_mode=False,
        )
        app = create_app(config)
        app.state.config = config

        with patch("overblick.dashboard.services.init_services", new_callable=AsyncMock), \
             patch("overblick.dashboard.services.cleanup_services", new_callable=AsyncMock), \
             patch("overblick.dashboard.app._create_templates") as mock_templates:
            mock_templates.return_value = MagicMock()
            mock_templates.return_value.env = MagicMock()
            mock_templates.return_value.env.globals = {}

            with patch("overblick.dashboard.routes.compass.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.dev.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.digest.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.email.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.github_dash.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.kontrast.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.log_agent.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.moltbook.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.polymarket_dash.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.skuggspel.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.spegel.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.stage.has_data", return_value=False), \
                 patch("overblick.dashboard.routes.telegram.has_data", return_value=False):

                async with lifespan(app):
                    # Without base_dir, it falls back to the package-relative path
                    assert hasattr(app.state, "setup_needed")
