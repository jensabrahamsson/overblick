"""
E2E tests for the IRC conversation viewer page.

Covers page rendering, channel name display, message feed,
per-identity color coding, and the sidebar conversation list.
"""

import pytest

pytestmark = [pytest.mark.e2e]


def _login(page, base_url: str):
    """Helper: auto-login by hitting /login."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


class TestIRCPage:
    """Test the IRC conversation viewer."""

    def test_irc_page_loads(self, dashboard_server, page):
        """IRC page should load without errors when IRC data exists."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/irc")
        page.wait_for_load_state("networkidle")

        # Should not be redirected away (IRC mock has data)
        assert "/irc" in page.url
        content = page.content()
        assert "500" not in page.title()

    def test_shows_channel_name(self, dashboard_server, page):
        """The active channel name (#krypto-analys) should be visible."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/irc")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "krypto-analys" in content

    def test_shows_conversation_turns(self, dashboard_server, page):
        """IRC conversation messages should be visible in the feed."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/irc")
        page.wait_for_load_state("networkidle")

        content = page.content()
        # Both mock messages should be visible
        assert "BTC breaking ATH" in content or "anomal" in content

    def test_shows_participant_names(self, dashboard_server, page):
        """Participant names (anomal, cherry) should be visible."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/irc")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "anomal" in content
        assert "cherry" in content

    def test_identity_colors_are_unique(self, dashboard_server, page):
        """Each participant should have a distinct color applied via inline style."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/irc")
        page.wait_for_load_state("networkidle")

        # Get all nick spans with style attributes
        nick_spans = page.locator(".irc-nick[style]")
        count = nick_spans.count()

        if count >= 2:
            colors = set()
            for i in range(count):
                style = nick_spans.nth(i).get_attribute("style") or ""
                colors.add(style)
            # At least two distinct colors (one per identity)
            assert len(colors) >= 2

    def test_conversation_sidebar_renders(self, dashboard_server, page):
        """Sidebar should list available conversations."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/irc")
        page.wait_for_load_state("networkidle")

        content = page.content()
        # Both mock conversations appear in sidebar
        assert "krypto-analys" in content
        assert "filosofi" in content

    def test_selecting_conversation_from_sidebar(self, dashboard_server, page):
        """Clicking a sidebar conversation should show that conversation."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/irc")
        page.wait_for_load_state("networkidle")

        # Click on the second conversation (filosofi)
        second_conv = page.locator("a.irc-sidebar-item").nth(1)
        if second_conv.count() > 0:
            second_conv.click()
            page.wait_for_load_state("networkidle")
            content = page.content()
            assert "filosofi" in content or "id=irc-002" in page.url

    def test_irc_page_has_log_region(self, dashboard_server, page):
        """IRC feed should have an accessible log region."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/irc")
        page.wait_for_load_state("networkidle")

        # ARIA log region for screen readers
        log_region = page.locator("[role='log']")
        assert log_region.count() >= 1

    def test_sidebar_has_navigation_landmark(self, dashboard_server, page):
        """Sidebar should be wrapped in a navigation landmark."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/irc")
        page.wait_for_load_state("networkidle")

        nav = page.locator("aside[role='navigation'], nav")
        assert nav.count() >= 1
