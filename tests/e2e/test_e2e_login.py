"""
E2E tests for the login/logout flow.

Tests authentication, session handling, bad credentials, and redirects.
"""

import time

import pytest

pytestmark = [pytest.mark.e2e]


class TestAutoLogin:
    """Test auto-login when no password is configured."""

    def test_login_redirects_to_dashboard(self, dashboard_server, page):
        """Visiting /login should auto-create session and redirect to /."""
        page.goto(f"{dashboard_server}/login")
        page.wait_for_load_state("networkidle")

        # Should end up on the dashboard (redirected from /login)
        assert page.url.rstrip("/") == dashboard_server or "/login" not in page.url

    def test_dashboard_accessible_after_auto_login(self, dashboard_server, page):
        """Dashboard should be accessible without explicit login."""
        # First hit /login to get session cookie
        page.goto(f"{dashboard_server}/login")
        page.wait_for_load_state("networkidle")

        # Now access dashboard
        page.goto(dashboard_server)
        page.wait_for_load_state("networkidle")

        # Should see dashboard content (not login page)
        content = page.content()
        assert "Dashboard" in content or "Överblick" in content


class TestLogout:
    """Test logout flow."""

    def test_logout_redirects_to_login(self, dashboard_server, page):
        """Clicking logout should redirect to login page."""
        # Login first
        page.goto(f"{dashboard_server}/login")
        page.wait_for_load_state("networkidle")

        # Navigate to logout
        page.goto(f"{dashboard_server}/logout")
        page.wait_for_load_state("networkidle")

        # Should be on login page
        assert "/login" in page.url

    def test_logout_link_visible(self, dashboard_server, page):
        """Logout link should be visible in navigation."""
        page.goto(f"{dashboard_server}/login")
        page.wait_for_load_state("networkidle")
        page.goto(dashboard_server)
        page.wait_for_load_state("networkidle")

        logout_link = page.locator("a[href='/logout']")
        assert logout_link.count() >= 1


class TestNavigation:
    """Test navigation between pages."""

    def test_nav_links_present(self, dashboard_server, page):
        """All navigation links should be present."""
        page.goto(f"{dashboard_server}/login")
        page.wait_for_load_state("networkidle")
        page.goto(dashboard_server)
        page.wait_for_load_state("networkidle")

        nav = page.locator("nav")
        assert nav.count() >= 1

        # Check all expected nav links
        for path in ["/", "/identities", "/conversations", "/llm", "/system", "/audit"]:
            link = page.locator(f"nav a[href='{path}']")
            assert link.count() >= 1, f"Missing nav link for {path}"

    def test_active_nav_link_highlighted(self, dashboard_server, page):
        """Current page nav link should have 'active' class."""
        page.goto(f"{dashboard_server}/login")
        page.wait_for_load_state("networkidle")
        page.goto(dashboard_server)
        page.wait_for_load_state("networkidle")

        active_link = page.locator("nav a.active")
        assert active_link.count() >= 1
