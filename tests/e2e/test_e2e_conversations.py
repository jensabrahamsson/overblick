"""
E2E tests for the Conversations page.

Tests conversation listing, filtering, and detail view.
"""

import time

import pytest

pytestmark = [pytest.mark.e2e]


def _login(page, base_url: str):
    """Helper: auto-login by hitting /login."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


class TestConversationsPage:
    """Test the conversations listing page."""

    def test_conversations_page_loads(self, dashboard_server, page):
        """Conversations page should load."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/conversations")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "Conversations" in content or "conversations" in content.lower()

    def test_nav_active_on_conversations(self, dashboard_server, page):
        """Conversations nav link should be active."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/conversations")
        page.wait_for_load_state("networkidle")

        active = page.locator("nav a.active")
        assert active.count() >= 1
        assert "conversations" in active.first.get_attribute("href").lower()


class TestConversationsScreenshots:
    """Take screenshots for visual review."""

    def test_screenshot_conversations(self, dashboard_server, screenshot_dir, page):
        """Screenshot the conversations page."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/conversations")
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        page.screenshot(
            path=str(screenshot_dir / "conversations.png"),
            full_page=True,
        )
