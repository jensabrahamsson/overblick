"""
E2E tests for the Identities page.

Tests identity listing, detail view, and trait display.
"""

import time

import pytest

pytestmark = [pytest.mark.e2e]


def _login(page, base_url: str):
    """Helper: auto-login by hitting /login."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


class TestIdentitiesPage:
    """Test the identities listing page."""

    def test_identities_page_loads(self, dashboard_server, page):
        """Identities page should load."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/identities")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "Identities" in content or "identities" in content.lower()

    def test_identity_names_displayed(self, dashboard_server, page):
        """All identity names should appear on the page."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/identities")
        page.wait_for_load_state("networkidle")

        content = page.content()
        for name in ["Anomal", "Cherry", "Rost"]:
            assert name in content, f"Identity '{name}' not found on identities page"

    def test_identity_descriptions_visible(self, dashboard_server, page):
        """Identity descriptions should be visible."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/identities")
        page.wait_for_load_state("networkidle")

        content = page.content()
        # At least one description keyword should appear
        has_desc = any(word in content for word in ["humanist", "Relationship", "Reformed", "crypto"])
        assert has_desc, "No identity descriptions found"

    def test_nav_active_on_identities(self, dashboard_server, page):
        """Identities nav link should be active."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/identities")
        page.wait_for_load_state("networkidle")

        active = page.locator("nav a.active")
        assert active.count() >= 1
        assert "identities" in active.first.get_attribute("href").lower()


class TestIdentitiesScreenshots:
    """Take screenshots for visual review."""

    def test_screenshot_identities_list(self, dashboard_server, screenshot_dir, page):
        """Screenshot the identities list page."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/identities")
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        page.screenshot(
            path=str(screenshot_dir / "identities_list.png"),
            full_page=True,
        )

    def test_screenshot_identity_card(self, dashboard_server, screenshot_dir, page):
        """Screenshot an identity card on the list page."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/identities")
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        page.screenshot(
            path=str(screenshot_dir / "identities_cards.png"),
            full_page=True,
        )
