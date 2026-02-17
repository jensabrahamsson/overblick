"""
E2E tests for the main dashboard page.

Tests identity cards, status badges, and overall layout.
"""

import time

import pytest

pytestmark = [pytest.mark.e2e]


def _login(page, base_url: str):
    """Helper: auto-login by hitting /login."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


class TestDashboardLoad:
    """Test dashboard page loads correctly."""

    def test_dashboard_loads(self, dashboard_server, page):
        """Dashboard should load without errors."""
        _login(page, dashboard_server)
        page.goto(dashboard_server)
        page.wait_for_load_state("networkidle")

        assert "Överblick" in page.content()

    def test_dashboard_title(self, dashboard_server, page):
        """Page title should contain Överblick."""
        _login(page, dashboard_server)
        page.goto(dashboard_server)
        page.wait_for_load_state("networkidle")

        assert "Överblick" in page.title()

    def test_dark_theme_applied(self, dashboard_server, page):
        """Dashboard should use dark theme."""
        _login(page, dashboard_server)
        page.goto(dashboard_server)
        page.wait_for_load_state("networkidle")

        bg_color = page.evaluate(
            "getComputedStyle(document.body).backgroundColor"
        )
        assert "rgb(" in bg_color
        values = bg_color.replace("rgb(", "").replace(")", "").split(",")
        r, g, b = [int(v.strip()) for v in values]
        assert r < 50 and g < 50 and b < 50, f"Not dark theme: {bg_color}"


class TestIdentityCards:
    """Test identity/agent cards on dashboard."""

    def test_identity_cards_rendered(self, dashboard_server, page):
        """Agent identity cards should be rendered."""
        _login(page, dashboard_server)
        page.goto(dashboard_server)
        page.wait_for_load_state("networkidle")

        # Look for agent cards or identity information
        content = page.content()
        # At least some identity names should appear
        has_identity = any(name in content for name in ["Anomal", "Cherry", "Rost"])
        assert has_identity, "No identity names found on dashboard"

    def test_footer_present(self, dashboard_server, page):
        """Footer should be present with copyright."""
        _login(page, dashboard_server)
        page.goto(dashboard_server)
        page.wait_for_load_state("networkidle")

        footer = page.locator("footer")
        assert footer.count() >= 1
        footer_text = footer.text_content()
        assert "Överblick" in footer_text


class TestDashboardScreenshots:
    """Take screenshots for visual review."""

    def test_screenshot_dashboard(self, dashboard_server, screenshot_dir, page):
        """Take a screenshot of the dashboard for visual review."""
        _login(page, dashboard_server)
        page.goto(dashboard_server)
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        page.screenshot(
            path=str(screenshot_dir / "dashboard_main.png"),
            full_page=True,
        )
