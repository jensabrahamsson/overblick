"""Tests for the /dev dashboard route."""

import pytest

from overblick.dashboard.auth import SESSION_COOKIE


class TestDevRoute:
    """Tests for the Dev Agent dashboard tab."""

    @pytest.mark.asyncio
    async def test_dev_page_empty(self, client, session_cookie):
        """Dev page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/dev",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Dev Agent" in resp.text
        assert "No dev agent activity yet" in resp.text

    @pytest.mark.asyncio
    async def test_dev_page_with_data(self, client, session_cookie):
        """Dev page renders bugs and goals when data exists."""
        import overblick.dashboard.routes.dev as dev_mod

        mock_data = {
            "bugs": [
                {
                    "identity": "smed",
                    "title": "NullPointerException in auth module",
                    "status": "fixed",
                    "priority": 90,
                    "fix_attempts": 2,
                    "pr_url": "https://github.com/example-org/example-repo/pull/101",
                    "created_at": 1709100000,
                    "updated_at": 1709110000,
                },
                {
                    "identity": "smed",
                    "title": "Rate limit not applied to webhooks",
                    "status": "analyzing",
                    "priority": 70,
                    "fix_attempts": 0,
                    "pr_url": None,
                    "created_at": 1709105000,
                    "updated_at": 1709105000,
                },
            ],
            "goals": [
                {
                    "identity": "smed",
                    "name": "clear_bug_backlog",
                    "description": "Resolve all P80+ bugs",
                    "priority": 90,
                    "status": "active",
                    "progress": 0.5,
                },
            ],
            "stats": {
                "total_bugs": 10, "fixed": 6, "failed": 1,
                "in_progress": 2, "fix_attempts": 18, "prs_created": 5,
            },
        }

        original = dev_mod._load_dev_data
        dev_mod._load_dev_data = lambda req: mock_data
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/dev",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "NullPointerException" in resp.text
            assert "fixed" in resp.text
            assert "clear_bug_backlog" in resp.text
            assert "10" in resp.text  # total bugs
        finally:
            dev_mod._load_dev_data = original

    @pytest.mark.asyncio
    async def test_dev_requires_auth(self, client):
        """Dev page redirects without auth."""
        resp = await client.get("/dev", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_dev_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when no identity has dev_agent."""
        from overblick.dashboard.routes import dev
        monkeypatch.chdir(tmp_path)
        assert dev.has_data() is False

    def test_dev_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when dev_agent is configured."""
        from overblick.dashboard.routes import dev
        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "smed"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - dev_agent\n")
        assert dev.has_data() is True
