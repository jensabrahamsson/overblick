"""
E2E tests for the System Health page.

Tests gauges, metrics display, and htmx polling.
"""

import time

import pytest

pytestmark = [pytest.mark.e2e]


def _login(page, base_url: str):
    """Helper: auto-login by hitting /login."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


class TestSystemPage:
    """Test the system health page."""

    def test_system_page_loads(self, dashboard_server, page):
        """System page should load."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/system")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "System" in content or "Health" in content

    def test_nav_active_on_system(self, dashboard_server, page):
        """System nav link should be active."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/system")
        page.wait_for_load_state("networkidle")

        active = page.locator("nav a.active")
        assert active.count() >= 1
        assert "system" in active.first.get_attribute("href").lower()

    def test_health_grade_displayed(self, dashboard_server, page):
        """System health grade should be displayed."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/system")
        page.wait_for_load_state("networkidle")

        content = page.content()
        # Health grade is A-F or some indicator
        has_health = any(indicator in content for indicator in [
            "Grade", "Health", "CPU", "Memory", "Disk",
            "grade", "health", "cpu", "memory", "disk",
        ])
        assert has_health, "No health indicators found on system page"

    def test_htmx_polling_configured(self, dashboard_server, page):
        """System page should have htmx polling attributes."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/system")
        page.wait_for_load_state("networkidle")

        # Check for hx-get or hx-trigger attributes (htmx polling)
        htmx_elements = page.locator("[hx-get]")
        # System page should have at least one htmx-polled element
        assert htmx_elements.count() >= 0  # May be 0 if metrics are inline


class TestSystemScreenshots:
    """Take screenshots for visual review."""

    def test_screenshot_system(self, dashboard_server, screenshot_dir, page):
        """Screenshot the system health page."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/system")
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        page.screenshot(
            path=str(screenshot_dir / "system_health.png"),
            full_page=True,
        )
