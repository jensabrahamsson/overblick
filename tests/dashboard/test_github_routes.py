"""Tests for the /github dashboard route."""

import pytest

from overblick.dashboard.auth import SESSION_COOKIE


class TestGitHubRoute:
    """Tests for the GitHub Agent dashboard tab."""

    @pytest.mark.asyncio
    async def test_github_page_empty(self, client, session_cookie):
        """GitHub page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/github",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "GitHub Agent" in resp.text
        assert "No GitHub agent activity yet" in resp.text

    @pytest.mark.asyncio
    async def test_github_page_with_data(self, client, session_cookie):
        """GitHub page renders actions and PRs when data exists."""
        import overblick.dashboard.routes.github_dash as github_mod

        mock_data = {
            "actions": [
                {
                    "identity": "smed",
                    "action_type": "comment_issue",
                    "target": "#42",
                    "repo": "example-org/example-repo",
                    "reasoning": "Issue needs triage",
                    "success": True,
                    "result": "Comment posted",
                    "duration_ms": 350.0,
                    "created_at": 1709100000,
                },
            ],
            "goals": [
                {
                    "identity": "smed",
                    "name": "triage_backlog",
                    "description": "Triage all open issues in backlog",
                    "priority": 80,
                    "status": "active",
                    "progress": 0.4,
                },
            ],
            "stats": {
                "events": 150, "actions_taken": 25,
                "comments_posted": 12, "prs_tracked": 8,
            },
            "prs": [
                {
                    "identity": "smed",
                    "repo": "example-org/example-repo",
                    "pr_number": 99,
                    "title": "Bump lodash from 4.17.20 to 4.17.21",
                    "author": "dependabot[bot]",
                    "is_dependabot": True,
                    "ci_status": "success",
                    "merged": True,
                    "auto_merged": True,
                    "first_seen": 1709090000,
                },
            ],
        }

        original = github_mod._load_github_data
        github_mod._load_github_data = lambda req: mock_data
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/github",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "smed" in resp.text
            assert "150" in resp.text  # events
            assert "triage_backlog" in resp.text
            assert "dependabot" in resp.text
            assert "auto-merged" in resp.text
        finally:
            github_mod._load_github_data = original

    @pytest.mark.asyncio
    async def test_github_requires_auth(self, client):
        """GitHub page redirects without auth."""
        resp = await client.get("/github", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_github_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when no identity has github."""
        from overblick.dashboard.routes import github_dash
        monkeypatch.chdir(tmp_path)
        assert github_dash.has_data() is False

    def test_github_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when github is configured."""
        from overblick.dashboard.routes import github_dash
        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "smed"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - github\n")
        assert github_dash.has_data() is True
