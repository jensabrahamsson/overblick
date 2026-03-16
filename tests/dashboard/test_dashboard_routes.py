"""Tests for dashboard main page, partials, and agent actions."""

import json
from unittest.mock import MagicMock, patch

import pytest

from overblick.dashboard.auth import SESSION_COOKIE
from overblick.dashboard.routes.dashboard import (
    _build_agent_status_rows,
    _build_plugin_cards,
    _plugin_display_name,
    _read_plugin_states,
    _write_plugin_state,
)


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
        """Per-agent start: POST /agent/{identity}/{plugin}/start."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/agent/anomal/moltbook/start",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        assert "Anomal" in resp.text

    @pytest.mark.asyncio
    async def test_stop_agent(self, client, session_cookie):
        """Per-agent stop: POST /agent/{identity}/{plugin}/stop."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/agent/anomal/moltbook/stop",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        assert "Anomal" in resp.text

    @pytest.mark.asyncio
    async def test_identity_start(self, client, session_cookie):
        """Identity-level start: POST /agent/{name}/start."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/agent/anomal/start",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_identity_stop(self, client, session_cookie):
        """Identity-level stop: POST /agent/{name}/stop."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/agent/anomal/stop",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_start_agent_supervisor_offline(self, app, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        # Simulate supervisor offline
        app.state.supervisor_service.start_agent.return_value = {
            "success": False,
            "error": "Supervisor not reachable",
        }
        app.state.supervisor_service.get_status.return_value = None
        app.state.supervisor_service.get_agents.return_value = []

        resp = await client.post(
            "/agent/anomal/moltbook/start",
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
            "success": False,
            "error": "Supervisor not reachable",
        }
        app.state.supervisor_service.get_status.return_value = None
        app.state.supervisor_service.get_agents.return_value = []

        resp = await client.post(
            "/agent/anomal/moltbook/stop",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_start_agent_rejects_bad_csrf(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.post(
            "/agent/anomal/moltbook/start",
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

    @pytest.mark.asyncio
    async def test_start_agent_invalid_identity_name(self, client, session_cookie):
        """Invalid identity name returns 400."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/agent/INVALID!/moltbook/start",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_start_agent_invalid_plugin_name(self, client, session_cookie):
        """Invalid plugin name returns 400."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/agent/anomal/INVALID!/start",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_stop_agent_invalid_identity_name(self, client, session_cookie):
        """Invalid identity name for stop returns 400."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/agent/INVALID!/moltbook/stop",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_identity_start_invalid_name(self, client, session_cookie):
        """Identity start with invalid name returns 400."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/agent/INVALID!/start",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_identity_stop_invalid_name(self, client, session_cookie):
        """Identity stop with invalid name returns 400."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/agent/INVALID!/stop",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 400


class TestDashboardSetupRedirect:
    @pytest.mark.asyncio
    async def test_should_redirect_to_settings_when_setup_needed(self, app, client, session_cookie):
        app.state.setup_needed = True
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/", cookies={SESSION_COOKIE: cookie_value}, follow_redirects=False
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/settings/"


class TestMoltbookStatusPartial:
    @pytest.mark.asyncio
    async def test_should_render_moltbook_status(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/partials/moltbook-status",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200


class TestPluginDisplayName:
    def test_should_capitalize_normal_words(self):
        assert _plugin_display_name("moltbook") == "Moltbook"

    def test_should_uppercase_acronyms(self):
        assert _plugin_display_name("ai_digest") == "AI Digest"
        assert _plugin_display_name("llm_gateway") == "LLM Gateway"

    def test_should_handle_multiple_acronyms(self):
        assert _plugin_display_name("rss_api") == "RSS API"

    def test_should_handle_single_word(self):
        assert _plugin_display_name("telegram") == "Telegram"


class TestReadPluginStates:
    def test_should_return_empty_when_no_file(self, tmp_path):
        result = _read_plugin_states(tmp_path, "anomal")
        assert result == {}

    def test_should_read_valid_json(self, tmp_path):
        data_dir = tmp_path / "data" / "anomal"
        data_dir.mkdir(parents=True)
        (data_dir / "plugin_control.json").write_text('{"moltbook": "running"}')
        result = _read_plugin_states(tmp_path, "anomal")
        assert result == {"moltbook": "running"}

    def test_should_return_empty_on_invalid_json(self, tmp_path):
        data_dir = tmp_path / "data" / "anomal"
        data_dir.mkdir(parents=True)
        (data_dir / "plugin_control.json").write_text("invalid json")
        result = _read_plugin_states(tmp_path, "anomal")
        assert result == {}


class TestWritePluginState:
    def test_should_write_state(self, tmp_path):
        _write_plugin_state(tmp_path, "anomal", "moltbook", "running")
        path = tmp_path / "data" / "anomal" / "plugin_control.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data == {"moltbook": "running"}

    def test_should_update_existing_state(self, tmp_path):
        _write_plugin_state(tmp_path, "anomal", "moltbook", "running")
        _write_plugin_state(tmp_path, "anomal", "telegram", "stopped")
        path = tmp_path / "data" / "anomal" / "plugin_control.json"
        data = json.loads(path.read_text())
        assert data == {"moltbook": "running", "telegram": "stopped"}

    def test_should_handle_write_failure(self, tmp_path):
        with patch("overblick.dashboard.routes.dashboard.os.rename", side_effect=OSError("rename failed")):
            with pytest.raises(OSError, match="rename failed"):
                _write_plugin_state(tmp_path, "anomal", "moltbook", "running")

    def test_should_handle_write_failure_and_unlink_failure(self, tmp_path):
        with (
            patch("overblick.dashboard.routes.dashboard.os.rename", side_effect=OSError("rename failed")),
            patch("overblick.dashboard.routes.dashboard.os.unlink", side_effect=OSError("unlink failed")),
        ):
            with pytest.raises(OSError, match="rename failed"):
                _write_plugin_state(tmp_path, "anomal", "moltbook", "running")


class TestBuildPluginCards:
    def test_should_build_cards_from_identities(self):
        identities = [
            {"name": "anomal", "display_name": "Anomal", "plugins": ["moltbook"], "identity_ref": "anomal"},
            {"name": "cherry", "display_name": "Cherry", "plugins": ["moltbook", "telegram"], "identity_ref": "cherry"},
        ]
        agents = [
            {"name": "anomal", "state": "running"},
        ]
        cards = _build_plugin_cards(identities, agents)
        assert len(cards) == 2
        moltbook_card = next(c for c in cards if c["name"] == "moltbook")
        assert moltbook_card["agent_count"] == 2
        assert moltbook_card["running_count"] == 1

    def test_should_handle_empty_inputs(self):
        cards = _build_plugin_cards([], [])
        assert cards == []

    def test_should_include_big_five_traits(self):
        identities = [
            {
                "name": "anomal",
                "display_name": "Anomal",
                "plugins": ["moltbook"],
                "identity_ref": "anomal",
                "traits": {
                    "openness": 0.95,
                    "conscientiousness": 0.7,
                    "extraversion": 0.6,
                    "agreeableness": 0.5,
                    "neuroticism": 0.3,
                    "irrelevant_trait": 0.1,
                },
            },
        ]
        cards = _build_plugin_cards(identities, [])
        traits = cards[0]["agents"][0]["traits"]
        assert "openness" in traits
        assert "irrelevant_trait" not in traits


class TestBuildAgentStatusRows:
    def test_should_build_rows_for_running_agent(self, tmp_path):
        identities = [
            {"name": "anomal", "display_name": "Anomal", "plugins": ["moltbook"]},
        ]
        agents = [
            {"name": "anomal", "state": "running", "pid": 123, "uptime": 3600, "restart_count": 0},
        ]
        audit_svc = MagicMock()
        audit_svc.query.return_value = []
        rows = _build_agent_status_rows(identities, agents, audit_svc, tmp_path)
        assert len(rows) == 1
        assert rows[0]["state"] == "running"
        assert rows[0]["can_start"] is False
        assert rows[0]["can_stop"] is True

    def test_should_show_stopped_plugin(self, tmp_path):
        # Write a plugin control file marking moltbook as stopped
        data_dir = tmp_path / "data" / "anomal"
        data_dir.mkdir(parents=True)
        (data_dir / "plugin_control.json").write_text('{"moltbook": "stopped"}')

        identities = [
            {"name": "anomal", "display_name": "Anomal", "plugins": ["moltbook"]},
        ]
        agents = [
            {"name": "anomal", "state": "running", "pid": 123, "uptime": 3600, "restart_count": 0},
        ]
        rows = _build_agent_status_rows(identities, agents, base_dir=tmp_path)
        assert rows[0]["state"] == "stopped"
        assert rows[0]["can_start"] is True
        assert rows[0]["can_stop"] is False

    def test_should_skip_capability_plugins(self, tmp_path):
        identities = [
            {"name": "anomal", "display_name": "Anomal", "plugins": ["moltbook", "host_health"]},
        ]
        agents = []
        rows = _build_agent_status_rows(identities, agents, base_dir=tmp_path)
        assert len(rows) == 1
        assert rows[0]["plugin"] == "moltbook"

    def test_should_skip_identities_without_plugins(self, tmp_path):
        identities = [
            {"name": "anomal", "display_name": "Anomal", "plugins": []},
        ]
        rows = _build_agent_status_rows(identities, [], base_dir=tmp_path)
        assert len(rows) == 0

    def test_should_show_offline_when_process_not_running(self, tmp_path):
        identities = [
            {"name": "anomal", "display_name": "Anomal", "plugins": ["moltbook"]},
        ]
        agents = []
        rows = _build_agent_status_rows(identities, agents, base_dir=tmp_path)
        assert rows[0]["state"] == "offline"
        assert rows[0]["pid"] is None

    def test_should_include_last_action_from_audit(self, tmp_path):
        identities = [
            {"name": "anomal", "display_name": "Anomal", "plugins": ["moltbook"]},
        ]
        agents = [
            {"name": "anomal", "state": "running", "pid": 123, "uptime": 3600, "restart_count": 0},
        ]
        audit_svc = MagicMock()
        audit_svc.query.return_value = [
            {"action": "post_content", "category": "moltbook", "timestamp": 1700000000.0, "success": True},
        ]
        rows = _build_agent_status_rows(identities, agents, audit_svc, tmp_path)
        assert rows[0]["last_action"] is not None
        assert rows[0]["last_action"]["action"] == "post_content"

    def test_should_use_default_base_dir_when_none(self):
        identities = [
            {"name": "anomal", "display_name": "Anomal", "plugins": ["moltbook"]},
        ]
        agents = []
        rows = _build_agent_status_rows(identities, agents)
        assert len(rows) == 1

    def test_should_show_proc_state_when_not_running(self, tmp_path):
        identities = [
            {"name": "anomal", "display_name": "Anomal", "plugins": ["moltbook"]},
        ]
        agents = [
            {"name": "anomal", "state": "crashed", "pid": 123},
        ]
        rows = _build_agent_status_rows(identities, agents, base_dir=tmp_path)
        assert rows[0]["state"] == "crashed"

    def test_should_use_uptime_seconds_key(self, tmp_path):
        identities = [
            {"name": "anomal", "display_name": "Anomal", "plugins": ["moltbook"]},
        ]
        agents = [
            {"name": "anomal", "state": "running", "pid": 123, "uptime_seconds": 7200, "restart_count": 0},
        ]
        rows = _build_agent_status_rows(identities, agents, base_dir=tmp_path)
        assert rows[0]["uptime"] == 7200
