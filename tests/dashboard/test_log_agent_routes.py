"""Tests for the /logs dashboard route."""

import pytest

from overblick.dashboard.auth import SESSION_COOKIE


class TestLogAgentRoute:
    """Tests for the Log Agent dashboard tab."""

    @pytest.mark.asyncio
    async def test_log_agent_page_empty(self, client, session_cookie):
        """Log Agent page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/logs",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Log Agent" in resp.text
        assert "No log agent activity yet" in resp.text

    @pytest.mark.asyncio
    async def test_log_agent_page_with_data(self, client, session_cookie):
        """Log Agent page renders actions and ticks when data exists."""
        import overblick.dashboard.routes.log_agent as log_mod

        mock_data = {
            "actions": [
                {
                    "identity": "smed",
                    "action_type": "send_alert",
                    "target": "anomal",
                    "reasoning": "Critical error rate spike detected",
                    "success": True,
                    "result": "Alert sent via Telegram",
                    "error": None,
                    "duration_ms": 120.0,
                    "created_at": 1709100000,
                },
            ],
            "goals": [
                {
                    "identity": "smed",
                    "name": "monitor_error_rates",
                    "description": "Track error rate trends across all identities",
                    "priority": 85,
                    "status": "active",
                    "progress": 0.7,
                },
            ],
            "stats": {
                "total_ticks": 200, "actions_taken": 45,
                "alerts_sent": 12, "learnings": 8,
            },
            "ticks": [
                {
                    "identity": "smed",
                    "tick": 200,
                    "observations": 15,
                    "planned": 3,
                    "executed": 3,
                    "succeeded": 2,
                    "summary": "Scanned 5 identity logs, found 2 anomalies",
                    "duration_ms": 850.0,
                    "completed_at": 1709100000,
                },
            ],
        }

        original = log_mod._load_log_data
        log_mod._load_log_data = lambda req: mock_data
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/logs",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "smed" in resp.text
            assert "200" in resp.text  # total ticks / tick number
            assert "monitor_error_rates" in resp.text
            assert "send_alert" in resp.text
        finally:
            log_mod._load_log_data = original

    @pytest.mark.asyncio
    async def test_log_agent_requires_auth(self, client):
        """Log Agent page redirects without auth."""
        resp = await client.get("/logs", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_log_agent_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when no identity has log_agent."""
        from overblick.dashboard.routes import log_agent
        monkeypatch.chdir(tmp_path)
        assert log_agent.has_data() is False

    def test_log_agent_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when log_agent is configured."""
        from overblick.dashboard.routes import log_agent
        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "smed"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - log_agent\n")
        assert log_agent.has_data() is True
