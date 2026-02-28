"""Tests for the /digest dashboard route."""

import pytest

from overblick.dashboard.auth import SESSION_COOKIE


class TestDigestRoute:
    """Tests for the AI Digest dashboard tab."""

    @pytest.mark.asyncio
    async def test_digest_page_empty(self, client, session_cookie):
        """Digest page renders with no data."""
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/digest",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "AI Digest" in resp.text
        assert "No digest data available yet" in resp.text

    @pytest.mark.asyncio
    async def test_digest_page_with_data(self, client, session_cookie):
        """Digest page renders digest info when data exists."""
        import overblick.dashboard.routes.digest as digest_mod

        mock_data = {
            "digests": [
                {
                    "identity": "anomal",
                    "last_digest_date": "2026-02-28",
                    "feed_count": 5,
                    "article_count": 7,
                },
            ],
        }

        original = digest_mod._load_digest_data
        digest_mod._load_digest_data = lambda req: mock_data
        try:
            cookie_value, _ = session_cookie
            resp = await client.get(
                "/digest",
                cookies={SESSION_COOKIE: cookie_value},
            )
            assert resp.status_code == 200
            assert "anomal" in resp.text
            assert "2026-02-28" in resp.text
        finally:
            digest_mod._load_digest_data = original

    @pytest.mark.asyncio
    async def test_digest_requires_auth(self, client):
        """Digest page redirects without auth."""
        resp = await client.get("/digest", follow_redirects=False)
        assert resp.status_code in (302, 303)

    def test_digest_has_data_no_dir(self, tmp_path, monkeypatch):
        """has_data() returns False when no identity has ai_digest."""
        from overblick.dashboard.routes import digest
        monkeypatch.chdir(tmp_path)
        assert digest.has_data() is False

    def test_digest_has_data_with_config(self, tmp_path, monkeypatch):
        """has_data() returns True when ai_digest is configured."""
        from overblick.dashboard.routes import digest
        monkeypatch.chdir(tmp_path)
        ids = tmp_path / "overblick" / "identities" / "anomal"
        ids.mkdir(parents=True)
        (ids / "identity.yaml").write_text("plugins:\n  - ai_digest\n")
        assert digest.has_data() is True
