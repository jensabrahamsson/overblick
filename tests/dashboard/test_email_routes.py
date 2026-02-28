"""Tests for the /email dashboard route."""

import pytest

from overblick.dashboard.auth import SESSION_COOKIE


class TestEmailRoute:
    """Tests for the Email Agent dashboard tab."""

    @pytest.mark.asyncio
    async def test_email_page_empty(self, client, session_cookie):
        """Email page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/email",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Email Agent" in resp.text
        assert "No emails processed yet" in resp.text

    @pytest.mark.asyncio
    async def test_email_page_with_data(self, client, session_cookie):
        """Email page renders email records when data exists."""
        import overblick.dashboard.routes.email as email_mod

        mock_data = {
            "emails": [
                {
                    "identity": "stal",
                    "sender": "alice@example.com",
                    "subject": "Meeting tomorrow",
                    "intent": "notify",
                    "confidence": 0.92,
                    "action": "notified",
                    "created_at": 1709100000,
                },
            ],
            "stats": {"processed": 15, "replied": 3, "notified": 8, "ignored": 4},
            "reputation": [
                {
                    "identity": "stal",
                    "domain": "example.com",
                    "ignore_count": 2,
                    "notify_count": 5,
                    "reply_count": 3,
                    "auto_ignore": False,
                },
            ],
        }

        original = email_mod._load_email_data
        email_mod._load_email_data = lambda req: mock_data
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/email",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "alice@example.com" in resp.text
            assert "Meeting tomorrow" in resp.text
            assert "notify" in resp.text
            assert "15" in resp.text  # processed count
            assert "example.com" in resp.text  # reputation
        finally:
            email_mod._load_email_data = original

    @pytest.mark.asyncio
    async def test_email_requires_auth(self, client):
        """Email page redirects without auth."""
        resp = await client.get("/email", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_email_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when no identity has email_agent."""
        from overblick.dashboard.routes import email
        monkeypatch.chdir(tmp_path)
        assert email.has_data() is False

    def test_email_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when email_agent is configured."""
        from overblick.dashboard.routes import email
        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "stal"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - email_agent\n")
        assert email.has_data() is True
