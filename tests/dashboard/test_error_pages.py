"""Tests for branded error pages (404, 500, generic exceptions)."""

import pytest
from unittest.mock import MagicMock
from overblick.dashboard.auth import SESSION_COOKIE


class TestErrorPages:
    @pytest.mark.asyncio
    async def test_404_renders_branded_page(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/nonexistent-page-that-does-not-exist",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 404
        assert "404" in resp.text
        assert "Överblick" in resp.text
        assert "Back to Dashboard" in resp.text

    @pytest.mark.asyncio
    async def test_404_contains_github_issue_link(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/this-route-does-not-exist",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 404
        assert "github.com/jensabrahamsson/overblick/issues/new" in resp.text

    @pytest.mark.asyncio
    async def test_500_renders_branded_page(self, app, client, session_cookie):
        """Trigger a 500 by making a service raise an unhandled exception."""
        cookie_value, _ = session_cookie

        # Make identity service raise to trigger a 500 on the dashboard page
        app.state.identity_service.get_all_identities.side_effect = RuntimeError(
            "database connection lost"
        )

        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 500
        assert "500" in resp.text
        assert "Överblick" in resp.text
        assert "Something went wrong" in resp.text

    @pytest.mark.asyncio
    async def test_500_does_not_expose_exception_details(self, app, client, session_cookie):
        """Verify internal exception message is NOT leaked to the user."""
        cookie_value, _ = session_cookie

        app.state.identity_service.get_all_identities.side_effect = RuntimeError(
            "secret internal error details"
        )

        resp = await client.get("/", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 500
        assert "secret internal error details" not in resp.text

    @pytest.mark.asyncio
    async def test_error_page_has_correct_content_type(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/does-not-exist",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 404
        assert "text/html" in resp.headers.get("content-type", "")
