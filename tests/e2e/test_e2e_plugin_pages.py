"""
E2E tests for plugin dashboard pages.

Covers pages that previously had no E2E Playwright tests:
compass, kontrast, spegel, skuggspel, stage, psychology, moltbook, digest.
"""

import pytest

pytestmark = [pytest.mark.e2e]


def _login(page, base_url: str):
    """Helper: auto-login by hitting /login."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


def _check_page(page, base_url, path, expected_title_fragment, expected_content):
    """Common page check: loads, no 500, has expected content."""
    _login(page, base_url)
    page.goto(f"{base_url}{path}")
    page.wait_for_load_state("networkidle")

    html = page.content()

    # No server error
    assert "Internal Server Error" not in html, f"{path} returned 500"
    assert "Traceback" not in html, f"{path} shows Python traceback"

    # Expected title
    assert expected_title_fragment in html, (
        f"{path} missing '{expected_title_fragment}' in HTML"
    )

    # Expected content
    for content in expected_content:
        assert content in html, f"{path} missing '{content}' in HTML"

    # Dark theme
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    values = bg.replace("rgb(", "").replace(")", "").split(",")
    r, g, b = [int(v.strip()) for v in values]
    assert r < 50 and g < 50 and b < 50, f"{path} not dark theme: {bg}"

    # No raw Jinja2 tags
    text = page.inner_text("body")
    assert "{{" not in text, f"{path} has raw Jinja2 tags"

    return html


class TestCompassPage:
    def test_compass_loads(self, dashboard_server, page):
        _check_page(
            page, dashboard_server, "/compass",
            "Compass", ["Identity", "Drift"],
        )


class TestKontrastPage:
    def test_kontrast_loads(self, dashboard_server, page):
        _check_page(
            page, dashboard_server, "/kontrast",
            "Kontrast", ["perspective", "Kontrast"],
        )


class TestSpegelPage:
    def test_spegel_loads(self, dashboard_server, page):
        _check_page(
            page, dashboard_server, "/spegel",
            "Spegel", ["Spegel"],
        )


class TestSkuggspelPage:
    def test_skuggspel_loads(self, dashboard_server, page):
        _check_page(
            page, dashboard_server, "/skuggspel",
            "Skuggspel", ["shadow", "Skuggspel"],
        )


class TestStagePage:
    def test_stage_loads(self, dashboard_server, page):
        _check_page(
            page, dashboard_server, "/stage",
            "Stage", ["scenario", "Stage"],
        )


class TestPsychologyPage:
    def test_psychology_loads(self, dashboard_server, page):
        _check_page(
            page, dashboard_server, "/psychology",
            "Psychology", ["Psychology"],
        )


class TestMoltbookPage:
    def test_moltbook_loads(self, dashboard_server, page):
        _check_page(
            page, dashboard_server, "/moltbook",
            "Moltbook", ["Moltbook"],
        )


class TestDigestPage:
    def test_digest_loads(self, dashboard_server, page):
        _check_page(
            page, dashboard_server, "/digest",
            "Digest", ["Digest"],
        )


class TestMonitorPage:
    def test_monitor_loads(self, dashboard_server, page):
        _check_page(
            page, dashboard_server, "/monitor",
            "Monitor", ["Monitor"],
        )


class TestNoInlineOnclick:
    """Verify CSP compliance — no inline onclick handlers in any page."""

    PAGES = [
        "/", "/identities", "/llm", "/audit", "/system",
        "/compass", "/kontrast", "/spegel", "/skuggspel",
        "/stage", "/psychology", "/moltbook", "/digest",
        "/monitor", "/polymarket", "/irc",
    ]

    def test_no_inline_onclick(self, dashboard_server, page):
        _login(page, dashboard_server)
        violations = []
        for path in self.PAGES:
            page.goto(f"{dashboard_server}{path}")
            page.wait_for_load_state("networkidle")
            html = page.content()
            if 'onclick="' in html or "onclick='" in html:
                violations.append(path)
        assert not violations, f"Inline onclick found on pages: {violations}"
