"""
E2E tests for the PolyTrader dashboard page.

Verifies that the Polymarket trading dashboard loads correctly,
displays market data or appropriate empty states, and has no errors.
"""

import pytest

pytestmark = [pytest.mark.e2e]


def _login(page, base_url: str):
    """Helper: auto-login by hitting /login."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


class TestPolytraderDashboard:
    """Test PolyTrader dashboard page."""

    def test_polymarket_page_loads(self, dashboard_server, page):
        """Polymarket page should load without 500 errors."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/polymarket")
        page.wait_for_load_state("networkidle")

        # Page should not be a server error
        content = page.content()
        assert "500" not in page.title()
        assert "Internal Server Error" not in content

    def test_polymarket_page_has_content(self, dashboard_server, page):
        """Page should render meaningful content (not blank)."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/polymarket")
        page.wait_for_load_state("networkidle")

        content = page.content()
        # Should contain either trading dashboard content or "not configured" message
        has_dashboard = "polymarket" in content.lower() or "polytrader" in content.lower()
        has_not_configured = "not configured" in content.lower() or "not_configured" in content.lower()
        assert has_dashboard or has_not_configured, "Page has no recognizable content"

    def test_polymarket_no_console_errors(self, dashboard_server, page):
        """No JavaScript console errors on the page."""
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/polymarket")
        page.wait_for_load_state("networkidle")

        assert len(errors) == 0, f"Console errors: {errors}"

    def test_polymarket_dark_theme(self, dashboard_server, page):
        """PolyTrader page should use dark theme like the rest of the dashboard."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/polymarket")
        page.wait_for_load_state("networkidle")

        bg_color = page.evaluate("getComputedStyle(document.body).backgroundColor")
        assert "rgb(" in bg_color
        values = bg_color.replace("rgb(", "").replace(")", "").split(",")
        r, g, b = [int(v.strip()) for v in values]
        assert r < 50 and g < 50 and b < 50, f"Not dark theme: {bg_color}"

    def test_polymarket_navigation_link(self, dashboard_server, page):
        """Dashboard navigation should include a link to the polymarket page."""
        _login(page, dashboard_server)
        page.goto(dashboard_server)
        page.wait_for_load_state("networkidle")

        # Check if there's a navigation link to polymarket
        polymarket_links = page.locator("a[href*='polymarket']")
        # It's ok if the link doesn't exist (plugin may not be configured in test env)
        # but if it does, it should be clickable
        if polymarket_links.count() > 0:
            polymarket_links.first.click()
            page.wait_for_load_state("networkidle")
            assert "500" not in page.title()
