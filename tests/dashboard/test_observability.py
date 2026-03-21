"""Tests for Monitor (ex-Observability) dashboard routes."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.dashboard.auth import SESSION_COOKIE
from overblick.dashboard.routes.observability import (
    _agent_health_color,
    _fetch_gateway,
    _format_uptime,
    _load_local_plugin_map,
    router,
)


class TestMonitorEndpoints:
    """Verify all /monitor endpoints are registered."""

    def test_monitor_main_endpoint(self):
        """Main /monitor page endpoint exists."""
        paths = [r.path for r in router.routes]
        assert "/monitor" in paths

    def test_monitor_agents_strip_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/agents-strip" in paths

    def test_monitor_gateway_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/gateway" in paths

    def test_monitor_fleet_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/fleet" in paths

    def test_monitor_audit_activity_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/audit-activity" in paths

    def test_monitor_routing_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/routing" in paths

    def test_monitor_errors_endpoint(self):
        paths = [r.path for r in router.routes]
        assert "/monitor/errors" in paths

    def test_old_observability_not_registered(self):
        """Old /observability path should NOT exist."""
        paths = [r.path for r in router.routes]
        assert "/observability" not in paths


class TestFormatUptime:
    def test_should_return_dash_when_zero(self):
        assert _format_uptime(0) == "—"

    def test_should_return_dash_when_negative(self):
        assert _format_uptime(-10) == "—"

    def test_should_return_minutes_when_less_than_hour(self):
        assert _format_uptime(300) == "5m"

    def test_should_return_hours_and_minutes(self):
        assert _format_uptime(3661) == "1h 1m"

    def test_should_return_hours_and_zero_minutes(self):
        assert _format_uptime(3600) == "1h 0m"

    def test_should_return_zero_minutes_for_tiny_seconds(self):
        assert _format_uptime(30) == "0m"


class TestAgentHealthColor:
    def test_should_return_red_when_offline(self):
        assert _agent_health_color({"state": "offline"}, 0.0) == "red"

    def test_should_return_red_when_crashed(self):
        assert _agent_health_color({"state": "crashed"}, 0.0) == "red"

    def test_should_return_red_when_high_error_rate(self):
        assert _agent_health_color({"state": "running"}, 25.0) == "red"

    def test_should_return_amber_when_moderate_error_rate(self):
        assert _agent_health_color({"state": "running"}, 15.0) == "amber"

    def test_should_return_amber_when_restarts(self):
        assert _agent_health_color({"state": "running", "restart_count": 1}, 0.0) == "amber"

    def test_should_return_green_when_healthy(self):
        assert _agent_health_color({"state": "running", "restart_count": 0}, 5.0) == "green"

    def test_should_return_green_when_no_restart_count_key(self):
        assert _agent_health_color({"state": "running"}, 0.0) == "green"

    def test_should_return_red_when_error_rate_exactly_25(self):
        assert _agent_health_color({"state": "running"}, 25.0) == "red"

    def test_should_return_amber_when_error_rate_exactly_10(self):
        assert _agent_health_color({"state": "running"}, 10.0) == "amber"


class TestFetchGateway:
    @pytest.mark.asyncio
    async def test_should_return_json_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "healthy"}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("overblick.dashboard.routes.observability.httpx.AsyncClient", return_value=mock_client):
            result = await _fetch_gateway("http://127.0.0.1:8200/health")

        assert result == {"status": "healthy"}

    @pytest.mark.asyncio
    async def test_should_return_none_on_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("overblick.dashboard.routes.observability.httpx.AsyncClient", return_value=mock_client):
            result = await _fetch_gateway("http://127.0.0.1:8200/health")

        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_on_connect_error(self):
        import httpx

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("overblick.dashboard.routes.observability.httpx.AsyncClient", return_value=mock_client):
            result = await _fetch_gateway("http://127.0.0.1:8200/health")

        assert result is None

    @pytest.mark.asyncio
    async def test_should_return_none_on_timeout(self):
        import httpx

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.TimeoutException("timeout")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("overblick.dashboard.routes.observability.httpx.AsyncClient", return_value=mock_client):
            result = await _fetch_gateway("http://127.0.0.1:8200/health")

        assert result is None


class TestLoadLocalPluginMap:
    def _reset_cache(self):
        import overblick.dashboard.routes.observability as obs_mod

        original = obs_mod._local_plugin_cache
        obs_mod._local_plugin_cache = None
        return obs_mod, original

    def test_should_return_cached_value(self):
        import overblick.dashboard.routes.observability as obs_mod

        original = obs_mod._local_plugin_cache
        try:
            obs_mod._local_plugin_cache = {"anomal": ["moltbook"]}
            result = _load_local_plugin_map()
            assert result == {"anomal": ["moltbook"]}
        finally:
            obs_mod._local_plugin_cache = original

    def test_should_return_empty_when_no_config_file(self, tmp_path):
        obs_mod, original = self._reset_cache()
        try:
            # __file__ resolution: Path(__file__).parent.parent.parent.parent / "config" / "overblick.yaml"
            # We mock __file__ so parent chain leads to tmp_path (no config dir)
            fake_file = str(tmp_path / "overblick" / "dashboard" / "routes" / "observability.py")
            with patch.object(obs_mod, "__file__", fake_file):
                result = _load_local_plugin_map()
                assert result == {}
        finally:
            obs_mod._local_plugin_cache = original

    def test_should_load_from_yaml(self, tmp_path):
        obs_mod, original = self._reset_cache()
        try:
            config_dir = tmp_path / "config"
            config_dir.mkdir()
            (config_dir / "overblick.yaml").write_text(
                "local_plugins:\n  anomal:\n    - moltbook\n    - telegram\n"
            )
            fake_file = str(tmp_path / "overblick" / "dashboard" / "routes" / "observability.py")
            with patch.object(obs_mod, "__file__", fake_file):
                result = _load_local_plugin_map()
                assert result == {"anomal": ["moltbook", "telegram"]}
        finally:
            obs_mod._local_plugin_cache = original

    def test_should_return_empty_on_yaml_error(self, tmp_path):
        obs_mod, original = self._reset_cache()
        try:
            config_dir = tmp_path / "config"
            config_dir.mkdir()
            (config_dir / "overblick.yaml").write_text("invalid: yaml: [[[")
            fake_file = str(tmp_path / "overblick" / "dashboard" / "routes" / "observability.py")
            with patch.object(obs_mod, "__file__", fake_file):
                result = _load_local_plugin_map()
                assert result == {}
        finally:
            obs_mod._local_plugin_cache = original

    def test_should_return_empty_when_no_local_plugins_key(self, tmp_path):
        obs_mod, original = self._reset_cache()
        try:
            config_dir = tmp_path / "config"
            config_dir.mkdir()
            (config_dir / "overblick.yaml").write_text("framework:\n  name: test\n")
            fake_file = str(tmp_path / "overblick" / "dashboard" / "routes" / "observability.py")
            with patch.object(obs_mod, "__file__", fake_file):
                result = _load_local_plugin_map()
                assert result == {}
        finally:
            obs_mod._local_plugin_cache = original


class TestMonitorPage:
    @pytest.mark.asyncio
    async def test_should_render_monitor_page(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/monitor", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Monitor" in resp.text or "monitor" in resp.text


class TestAgentsStripPartial:
    @pytest.mark.asyncio
    async def test_should_render_agents_strip(self, client, session_cookie, mock_audit_service):
        mock_audit_service.count_with_failures = MagicMock(return_value=(100, 5))
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/agents-strip", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_should_handle_zero_total_events(self, client, session_cookie, mock_audit_service):
        mock_audit_service.count_with_failures = MagicMock(return_value=(0, 0))
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/agents-strip", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_should_show_supervisor_down(
        self, app, client, session_cookie, mock_audit_service
    ):
        mock_audit_service.count_with_failures = MagicMock(return_value=(0, 0))
        app.state.supervisor_service.get_status.return_value = None
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/agents-strip", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200


class TestGatewayPartial:
    @pytest.mark.asyncio
    async def test_should_render_when_gateway_available(self, client, session_cookie):
        health = {"status": "healthy", "default_backend": "local", "backends": {"local": "ok"}}
        stats = {"requests_processed": 100, "requests_high_priority": 20, "requests_low_priority": 80, "uptime_seconds": 7200}
        with patch("overblick.dashboard.routes.observability._fetch_gateway", side_effect=[
            health, stats
        ]):
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/monitor/gateway", cookies={SESSION_COOKIE: cookie_value}
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_should_render_when_gateway_unavailable(self, client, session_cookie):
        with patch("overblick.dashboard.routes.observability._fetch_gateway", return_value=None):
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/monitor/gateway", cookies={SESSION_COOKIE: cookie_value}
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_should_handle_backend_dict_info(self, client, session_cookie):
        health = {
            "status": "healthy",
            "default_backend": "local",
            "backends": {
                "local": {"status": "ok", "type": "ollama", "model": "qwen3:8b"},
            },
        }
        with patch("overblick.dashboard.routes.observability._fetch_gateway", side_effect=[
            health, None
        ]):
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/monitor/gateway", cookies={SESSION_COOKIE: cookie_value}
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_should_handle_stats_only(self, client, session_cookie):
        stats = {"requests_processed": 10, "requests_high_priority": 5, "requests_low_priority": 5, "uptime_seconds": 600}
        with patch("overblick.dashboard.routes.observability._fetch_gateway", side_effect=[
            None, stats
        ]):
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/monitor/gateway", cookies={SESSION_COOKIE: cookie_value}
            )
            assert resp.status_code == 200


class TestFleetPartial:
    @pytest.mark.asyncio
    async def test_should_render_fleet_table(self, client, session_cookie):
        import overblick.dashboard.routes.observability as obs_mod

        original = obs_mod._local_plugin_cache
        try:
            obs_mod._local_plugin_cache = {}
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/monitor/fleet", cookies={SESSION_COOKIE: cookie_value}
            )
            assert resp.status_code == 200
        finally:
            obs_mod._local_plugin_cache = original

    @pytest.mark.asyncio
    async def test_should_merge_local_plugins(self, app, client, session_cookie):
        import overblick.dashboard.routes.observability as obs_mod

        original = obs_mod._local_plugin_cache
        try:
            obs_mod._local_plugin_cache = {"anomal": ["extra_plugin"]}
            app.state.supervisor_service.get_agents.return_value = [
                {
                    "name": "anomal",
                    "state": "running",
                    "pid": 12345,
                    "uptime": 3600,
                    "restart_count": 0,
                    "plugins": ["moltbook"],
                },
            ]
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/monitor/fleet", cookies={SESSION_COOKIE: cookie_value}
            )
            assert resp.status_code == 200
        finally:
            obs_mod._local_plugin_cache = original

    @pytest.mark.asyncio
    async def test_should_handle_uptime_seconds_key(self, app, client, session_cookie):
        import overblick.dashboard.routes.observability as obs_mod

        original = obs_mod._local_plugin_cache
        try:
            obs_mod._local_plugin_cache = {}
            app.state.supervisor_service.get_agents.return_value = [
                {
                    "name": "anomal",
                    "state": "running",
                    "pid": 12345,
                    "uptime_seconds": 7200,
                    "restart_count": 0,
                },
            ]
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/monitor/fleet", cookies={SESSION_COOKIE: cookie_value}
            )
            assert resp.status_code == 200
        finally:
            obs_mod._local_plugin_cache = original

    @pytest.mark.asyncio
    async def test_should_show_supervisor_offline(self, app, client, session_cookie):
        import overblick.dashboard.routes.observability as obs_mod

        original = obs_mod._local_plugin_cache
        try:
            obs_mod._local_plugin_cache = {}
            app.state.supervisor_service.get_status.return_value = None
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/monitor/fleet", cookies={SESSION_COOKIE: cookie_value}
            )
            assert resp.status_code == 200
        finally:
            obs_mod._local_plugin_cache = original


class TestAuditActivityPartial:
    @pytest.mark.asyncio
    async def test_should_render_audit_activity(self, client, session_cookie, mock_audit_service):
        mock_audit_service.count_by_hour = MagicMock(return_value=[0] * 12)
        mock_audit_service.count_by_category = MagicMock(return_value={"llm": 10, "moltbook": 5})
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/audit-activity", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_should_handle_zero_total(self, client, session_cookie, mock_audit_service):
        mock_audit_service.count.return_value = 0
        mock_audit_service.count_by_hour = MagicMock(return_value=[])
        mock_audit_service.count_by_category = MagicMock(return_value={})
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/audit-activity", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_should_calculate_error_rate(self, client, session_cookie, mock_audit_service):
        # count is called multiple times with different kwargs
        def count_side_effect(**kwargs):
            if kwargs.get("success") is False:
                return 10
            if kwargs.get("category") == "llm":
                return 20
            return 100

        mock_audit_service.count.side_effect = count_side_effect
        mock_audit_service.count_by_hour = MagicMock(return_value=[5] * 12)
        mock_audit_service.count_by_category = MagicMock(return_value={"llm": 20})
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/audit-activity", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200


class TestRoutingPartial:
    @pytest.mark.asyncio
    async def test_should_render_routing_with_status(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/routing", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_should_handle_supervisor_offline(self, app, client, session_cookie):
        app.state.supervisor_service.get_status.return_value = None
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/routing", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_should_include_routing_data(self, app, client, session_cookie):
        app.state.supervisor_service.get_status.return_value = {
            "state": "running",
            "routing": {"messages_routed": 42, "last_route_time": time.time()},
        }
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/routing", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200


class TestErrorsPartial:
    @pytest.mark.asyncio
    async def test_should_render_errors(self, client, session_cookie, mock_audit_service):
        mock_audit_service.query.return_value = [
            {"id": 1, "action": "fail_action", "success": False, "timestamp": time.time()},
        ]
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/errors", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_should_filter_successes(self, client, session_cookie, mock_audit_service):
        mock_audit_service.query.return_value = [
            {"id": 1, "action": "ok_action", "success": True, "timestamp": time.time()},
            {"id": 2, "action": "fail_action", "success": False, "timestamp": time.time()},
        ]
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/errors", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_should_render_empty_errors(self, client, session_cookie, mock_audit_service):
        mock_audit_service.query.return_value = []
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/monitor/errors", cookies={SESSION_COOKIE: cookie_value}
        )
        assert resp.status_code == 200
