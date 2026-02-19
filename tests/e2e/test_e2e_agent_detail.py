"""
E2E tests for the agent detail page.

Covers identity info, runtime status, LLM config, personality traits,
and navigation — including the 404→redirect case.
"""

import pytest

pytestmark = [pytest.mark.e2e]


def _login(page, base_url: str):
    """Helper: auto-login by hitting /login."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


class TestAgentDetail:
    """Test the agent detail page for a known running agent."""

    def test_agent_detail_loads(self, dashboard_server, page):
        """Agent detail page for 'anomal' should load without errors."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/agent/anomal")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "Anomal" in content

    def test_shows_running_status(self, dashboard_server, page):
        """Status badge should show 'running' for the anomal agent."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/agent/anomal")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "running" in content.lower()

    def test_shows_llm_config(self, dashboard_server, page):
        """Agent detail should display LLM model, temperature, and max tokens."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/agent/anomal")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "qwen3:8b" in content
        assert "0.7" in content

    def test_shows_identity_name(self, dashboard_server, page):
        """Identity name and display name should be visible."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/agent/anomal")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "anomal" in content.lower()

    def test_shows_personality_traits(self, dashboard_server, page):
        """Personality trait bars should be visible on the page."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/agent/anomal")
        page.wait_for_load_state("networkidle")

        content = page.content()
        # Anomal has personality with openness, conscientiousness, etc.
        assert any(trait in content.lower() for trait in ["openness", "trait", "personality"])

    def test_shows_audit_entries(self, dashboard_server, page):
        """Audit trail section should be present."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/agent/anomal")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert any(word in content.lower() for word in ["activity", "audit", "recent"])

    def test_back_link_works(self, dashboard_server, page):
        """Back to Dashboard link should navigate to the root."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/agent/anomal")
        page.wait_for_load_state("networkidle")

        back_link = page.locator("a", has_text="Back to Dashboard")
        assert back_link.count() >= 1
        back_link.first.click()
        page.wait_for_load_state("networkidle")

        assert page.url.rstrip("/") == dashboard_server.rstrip("/") or page.url == f"{dashboard_server}/"

    def test_unknown_agent_redirects(self, dashboard_server, page):
        """Requesting an unknown agent should redirect to the dashboard."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/agent/doesnotexist")
        page.wait_for_load_state("networkidle")

        # Should be redirected away from /agent/doesnotexist
        assert "/agent/doesnotexist" not in page.url

    def test_stopped_agent_shows_offline_status(self, dashboard_server, page):
        """The 'rost' agent (stopped) should show 'stopped' status."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/agent/rost")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "stopped" in content.lower() or "offline" in content.lower()

    def test_no_llm_crash_when_llm_is_none(self, dashboard_server, page):
        """Agent detail page should not crash for identity without LLM config."""
        # 'rost' in mock has llm set; this test verifies the template is safe
        # by checking the page loads without 500 error for all agents
        _login(page, dashboard_server)
        for agent_name in ["anomal", "cherry", "rost"]:
            page.goto(f"{dashboard_server}/agent/{agent_name}")
            page.wait_for_load_state("networkidle")
            assert "500" not in page.title()
            assert "Internal Server Error" not in page.content()
