"""
E2E tests for Compass and Skuggspel dashboard pages.

Tests visual structure, accessibility attributes, responsive layout,
and overall page rendering after the UI redesign.
"""

import time

import pytest

pytestmark = [pytest.mark.e2e]


def _login(page, base_url: str):
    """Helper: auto-login by hitting /login."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


# ── Compass Tests ──


class TestCompassPageLoads:
    """Test Compass page loads with correct structure."""

    def test_compass_page_loads(self, dashboard_server, page):
        """Compass page should load with title, status strip, and grid."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/compass")
        page.wait_for_load_state("networkidle")

        assert "Compass" in page.title()
        content = page.content()
        assert "Identity drift detection" in content
        assert page.locator(".compass-status-strip").count() == 1
        assert page.locator(".compass-grid").count() == 1

    def test_compass_drift_gauges_rendered(self, dashboard_server, page):
        """Drift gauges should have role=meter and ARIA attributes."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/compass")
        page.wait_for_load_state("networkidle")

        gauges = page.locator('[role="meter"]')
        assert gauges.count() >= 2  # anomal + cherry

        first_gauge = gauges.first
        assert first_gauge.get_attribute("aria-valuenow") is not None
        assert first_gauge.get_attribute("aria-valuemin") == "0"
        assert first_gauge.get_attribute("aria-label") is not None

    def test_compass_alert_stack_severity(self, dashboard_server, page):
        """Alerts should be sorted by severity with visible badges."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/compass")
        page.wait_for_load_state("networkidle")

        alert_stack = page.locator(".alert-stack")
        assert alert_stack.count() == 1

        alerts = page.locator(".alert-item")
        assert alerts.count() >= 2

        # First alert should be critical (sorted by severity)
        first_alert = alerts.first
        assert "alert-item--critical" in first_alert.get_attribute("class")

        # Severity badges should be visible
        badges = page.locator(".alert-item .badge-state")
        assert badges.count() >= 2

    def test_compass_status_strip_dots(self, dashboard_server, page):
        """Status strip should show identity dots with ARIA labels."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/compass")
        page.wait_for_load_state("networkidle")

        dots = page.locator(".identity-dot-row .status-dot")
        assert dots.count() >= 2

        first_dot = dots.first
        aria_label = first_dot.get_attribute("aria-label")
        assert aria_label is not None
        assert "drift" in aria_label

    def test_compass_responsive_layout(self, dashboard_server, page):
        """At 375px viewport, compass grid should be single column."""
        _login(page, dashboard_server)
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{dashboard_server}/compass")
        page.wait_for_load_state("networkidle")

        grid = page.locator(".compass-grid")
        grid_style = grid.evaluate(
            "el => getComputedStyle(el).gridTemplateColumns"
        )
        # Single column should have only one value (no space-separated columns)
        column_count = len(grid_style.split(" "))
        assert column_count == 1, f"Expected 1 column at 375px, got: {grid_style}"

    def test_compass_screenshot(self, dashboard_server, screenshot_dir, page):
        """Take a screenshot of the Compass page for visual review."""
        _login(page, dashboard_server)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{dashboard_server}/compass")
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        page.screenshot(
            path=str(screenshot_dir / "compass_full.png"),
            full_page=True,
        )


# ── Skuggspel Tests ──


class TestSkuggspelPageLoads:
    """Test Skuggspel page loads with correct structure."""

    def test_skuggspel_page_loads(self, dashboard_server, page):
        """Skuggspel page should load with title and purple header."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/skuggspel")
        page.wait_for_load_state("networkidle")

        assert "Skuggspel" in page.title()
        content = page.content()
        assert "Shadow-self content" in content
        assert page.locator(".skuggspel-header").count() == 1

    def test_skuggspel_shadow_cards(self, dashboard_server, page):
        """Shadow cards should render with italic content and purple borders."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/skuggspel")
        page.wait_for_load_state("networkidle")

        cards = page.locator(".shadow-card")
        assert cards.count() >= 2

        # Cards should have shadow-content with italic text
        content = page.locator(".shadow-content")
        assert content.count() >= 2
        first_style = content.first.evaluate(
            "el => getComputedStyle(el).fontStyle"
        )
        assert first_style == "italic"

    def test_skuggspel_accessibility(self, dashboard_server, page):
        """Shadow cards should use article elements with aria-labelledby."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/skuggspel")
        page.wait_for_load_state("networkidle")

        articles = page.locator("article.shadow-card")
        assert articles.count() >= 2

        first_article = articles.first
        labelledby = first_article.get_attribute("aria-labelledby")
        assert labelledby is not None
        assert labelledby.startswith("shadow-title-")

        # Referenced heading should exist
        heading = page.locator(f"#{labelledby}")
        assert heading.count() == 1

    def test_skuggspel_meta_info(self, dashboard_server, page):
        """Shadow cards should display framework and word count metadata."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/skuggspel")
        page.wait_for_load_state("networkidle")

        meta_sections = page.locator(".shadow-meta")
        assert meta_sections.count() >= 2

        content = page.content()
        assert "jungian_inversion" in content
        assert "default_inversion" in content

    def test_skuggspel_responsive_layout(self, dashboard_server, page):
        """At 375px viewport, shadow cards should stack."""
        _login(page, dashboard_server)
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{dashboard_server}/skuggspel")
        page.wait_for_load_state("networkidle")

        grid = page.locator(".detail-grid")
        grid_style = grid.evaluate(
            "el => getComputedStyle(el).gridTemplateColumns"
        )
        column_count = len(grid_style.split(" "))
        assert column_count == 1, f"Expected 1 column at 375px, got: {grid_style}"

    def test_skuggspel_screenshot(self, dashboard_server, screenshot_dir, page):
        """Take a screenshot of the Skuggspel page for visual review."""
        _login(page, dashboard_server)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{dashboard_server}/skuggspel")
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        page.screenshot(
            path=str(screenshot_dir / "skuggspel_full.png"),
            full_page=True,
        )
