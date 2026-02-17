"""Tests for Moltbook status dashboard partial and service method."""

import json

import pytest
from overblick.dashboard.auth import SESSION_COOKIE
from overblick.dashboard.services.system import SystemService


class TestMoltbookStatusService:
    def test_get_moltbook_statuses_empty_no_dir(self, tmp_path):
        svc = SystemService(tmp_path)
        assert svc.get_moltbook_statuses() == []

    def test_get_moltbook_statuses_empty_no_files(self, tmp_path):
        (tmp_path / "data" / "anomal").mkdir(parents=True)
        svc = SystemService(tmp_path)
        assert svc.get_moltbook_statuses() == []

    def test_get_moltbook_statuses_reads_json(self, tmp_path):
        identity_dir = tmp_path / "data" / "anomal"
        identity_dir.mkdir(parents=True)
        status = {"status": "active", "detail": "", "updated_at": "2025-01-01T00:00:00"}
        (identity_dir / "moltbook_status.json").write_text(json.dumps(status))

        svc = SystemService(tmp_path)
        result = svc.get_moltbook_statuses()
        assert len(result) == 1
        assert result[0]["status"] == "active"
        assert result[0]["identity"] == "anomal"

    def test_get_moltbook_statuses_suspended(self, tmp_path):
        identity_dir = tmp_path / "data" / "cherry"
        identity_dir.mkdir(parents=True)
        status = {"status": "suspended", "detail": "Banned for spam", "updated_at": "2025-01-02T00:00:00"}
        (identity_dir / "moltbook_status.json").write_text(json.dumps(status))

        svc = SystemService(tmp_path)
        result = svc.get_moltbook_statuses()
        assert len(result) == 1
        assert result[0]["status"] == "suspended"
        assert result[0]["detail"] == "Banned for spam"

    def test_get_moltbook_statuses_multiple_identities(self, tmp_path):
        for name, status in [("anomal", "active"), ("cherry", "suspended")]:
            d = tmp_path / "data" / name
            d.mkdir(parents=True)
            (d / "moltbook_status.json").write_text(json.dumps({"status": status}))

        svc = SystemService(tmp_path)
        result = svc.get_moltbook_statuses()
        assert len(result) == 2

    def test_get_moltbook_statuses_ignores_corrupt_json(self, tmp_path):
        d = tmp_path / "data" / "broken"
        d.mkdir(parents=True)
        (d / "moltbook_status.json").write_text("not json!")

        svc = SystemService(tmp_path)
        assert svc.get_moltbook_statuses() == []


class TestMoltbookStatusPartial:
    @pytest.mark.asyncio
    async def test_moltbook_status_endpoint_returns_html(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/partials/moltbook-status",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        # mock_system_service returns [] for get_moltbook_statuses by default
        assert "No Moltbook agents" in resp.text

    @pytest.mark.asyncio
    async def test_moltbook_status_with_active_agent(self, app, client, session_cookie):
        cookie_value, _ = session_cookie
        app.state.system_service.get_moltbook_statuses.return_value = [
            {"identity": "anomal", "status": "active", "detail": "", "updated_at": "2025-01-01T00:00:00"},
        ]
        resp = await client.get(
            "/partials/moltbook-status",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Anomal" in resp.text
        assert "Active" in resp.text

    @pytest.mark.asyncio
    async def test_moltbook_status_with_suspended_agent(self, app, client, session_cookie):
        cookie_value, _ = session_cookie
        app.state.system_service.get_moltbook_statuses.return_value = [
            {"identity": "cherry", "status": "suspended", "detail": "Banned", "updated_at": "2025-01-02T00:00:00"},
        ]
        resp = await client.get(
            "/partials/moltbook-status",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Cherry" in resp.text
        assert "Suspended" in resp.text
        assert "Banned" in resp.text

    @pytest.mark.asyncio
    async def test_dashboard_includes_moltbook_status(self, app, client, session_cookie):
        """Main dashboard page includes the Moltbook status section."""
        cookie_value, _ = session_cookie
        app.state.system_service.get_moltbook_statuses.return_value = [
            {"identity": "anomal", "status": "active", "detail": "", "updated_at": ""},
        ]
        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Moltbook Status" in resp.text
        assert "moltbook-status-container" in resp.text
