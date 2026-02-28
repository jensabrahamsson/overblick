"""Tests for the /telegram dashboard route."""

import pytest

from overblick.dashboard.auth import SESSION_COOKIE


class TestTelegramRoute:
    """Tests for the Telegram dashboard tab."""

    @pytest.mark.asyncio
    async def test_telegram_page_empty(self, client, session_cookie):
        """Telegram page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/telegram",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Telegram" in resp.text
        assert "No Telegram notifications sent yet" in resp.text

    @pytest.mark.asyncio
    async def test_telegram_page_with_data(self, client, session_cookie):
        """Telegram page renders notifications when data exists."""
        import overblick.dashboard.routes.telegram as telegram_mod

        mock_data = {
            "notifications": [
                {
                    "identity": "stal",
                    "text": "New email from alice@example.com: Meeting tomorrow",
                    "feedback": True,
                    "feedback_text": "Reply: Yes, I'll be there",
                    "is_draft": False,
                    "created_at": 1709100000,
                },
            ],
            "stats": {"sent": 42, "feedback_received": 15, "identities": 1},
        }

        original = telegram_mod._load_telegram_data
        telegram_mod._load_telegram_data = lambda req: mock_data
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/telegram",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "stal" in resp.text
            assert "42" in resp.text  # sent count
            assert "15" in resp.text  # feedback count
        finally:
            telegram_mod._load_telegram_data = original

    @pytest.mark.asyncio
    async def test_telegram_requires_auth(self, client):
        """Telegram page redirects without auth."""
        resp = await client.get("/telegram", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_telegram_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when no identity has telegram."""
        from overblick.dashboard.routes import telegram
        monkeypatch.chdir(tmp_path)
        assert telegram.has_data() is False

    def test_telegram_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when telegram is configured."""
        from overblick.dashboard.routes import telegram
        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "cherry"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - telegram\n")
        assert telegram.has_data() is True
