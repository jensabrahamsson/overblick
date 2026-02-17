"""
E2E tests for the LLM page.

Tests model listing and stats display.
"""

import time

import pytest

pytestmark = [pytest.mark.e2e]


def _login(page, base_url: str):
    """Helper: auto-login by hitting /login."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


class TestLLMPage:
    """Test the LLM page."""

    def test_llm_page_loads(self, dashboard_server, page):
        """LLM page should load."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/llm")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "LLM" in content or "llm" in content.lower() or "Model" in content

    def test_nav_active_on_llm(self, dashboard_server, page):
        """LLM nav link should be active."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/llm")
        page.wait_for_load_state("networkidle")

        active = page.locator("nav a.active")
        assert active.count() >= 1
        assert "llm" in active.first.get_attribute("href").lower()


class TestLLMScreenshots:
    """Take screenshots for visual review."""

    def test_screenshot_llm(self, dashboard_server, screenshot_dir, page):
        """Screenshot the LLM page."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/llm")
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        page.screenshot(
            path=str(screenshot_dir / "llm_page.png"),
            full_page=True,
        )
