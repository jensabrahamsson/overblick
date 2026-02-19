"""
E2E tests for the 7-step onboarding wizard.

Covers step rendering, validation, navigation, and the full wizard flow.
"""

import pytest

pytestmark = [pytest.mark.e2e]


def _login(page, base_url: str):
    """Helper: auto-login by hitting /login."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


def _login_with_csrf(page, base_url: str) -> str:
    """Login and extract CSRF token, setting it as a header for all subsequent requests.

    The middleware requires X-CSRF-Token on all POST requests.
    The CSRF token is embedded in every page's body hx-headers attribute.
    """
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")

    # Navigate to any authenticated page to get the CSRF token from hx-headers
    page.goto(f"{base_url}/onboard?step=1")
    page.wait_for_load_state("networkidle")

    csrf_token = page.evaluate("""
        () => {
            const body = document.body;
            if (!body) return '';
            const headers = body.getAttribute('hx-headers');
            if (!headers) return '';
            try {
                const parsed = JSON.parse(headers);
                return parsed['X-CSRF-Token'] || '';
            } catch (e) {
                return '';
            }
        }
    """)

    if csrf_token:
        page.set_extra_http_headers({"X-CSRF-Token": csrf_token})

    return csrf_token


class TestOnboardingWizard:
    """Test the onboarding wizard step by step."""

    def test_step1_loads(self, dashboard_server, page):
        """Step 1 (name) should render with a name input field."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/onboard?step=1")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "onboard" in page.url.lower() or "step" in content.lower()
        # Name input must be present
        assert page.locator("input[name='name']").count() == 1

    def test_step1_invalid_name_shows_error(self, dashboard_server, page):
        """Submitting a name starting with a digit should show a server-side validation error.

        The HTML input has a native `pattern` attribute that blocks most invalid input
        before it reaches the server. We bypass it via JS to test the server-side Pydantic
        validation.
        """
        _login_with_csrf(page, dashboard_server)

        # Disable browser-side pattern validation so the form actually submits to the server
        page.evaluate("""
            () => {
                const input = document.querySelector('input[name="name"]');
                if (input) {
                    input.removeAttribute('pattern');
                    input.removeAttribute('required');
                    input.value = '1invalid';
                }
            }
        """)
        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        content = page.content()
        # Server-side Pydantic rejects the name — should stay on step 1 with error
        assert page.locator("input[name='name']").count() == 1
        assert any(word in content.lower() for word in ["error", "invalid", "validation"])

    def test_step1_valid_name_advances(self, dashboard_server, page):
        """Valid name submission should advance to step 2."""
        _login_with_csrf(page, dashboard_server)

        page.fill("input[name='name']", "testbot")
        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # Should be redirected to step 2
        assert "step=2" in page.url or "llm" in page.content().lower()

    def test_step2_llm_config_loads(self, dashboard_server, page):
        """Step 2 (LLM config) should show model and temperature inputs."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/onboard?step=2")
        page.wait_for_load_state("networkidle")

        content = page.content()
        # LLM configuration page should mention model or temperature
        assert any(word in content.lower() for word in ["model", "temperature", "llm"])

    def test_step3_personality_loads(self, dashboard_server, page):
        """Step 3 (personality) should render with personality options."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/onboard?step=3")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert any(word in content.lower() for word in ["personality", "anomal", "character"])

    def test_step4_plugins_loads(self, dashboard_server, page):
        """Step 4 (plugins) should show plugin checkboxes."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/onboard?step=4")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert any(word in content.lower() for word in ["plugin", "moltbook", "telegram"])

    def test_step5_secrets_loads(self, dashboard_server, page):
        """Step 5 (secrets) should show the secrets form."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/onboard?step=5")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert any(word in content.lower() for word in ["secret", "api key", "encrypted"])

    def test_step5_secrets_value_has_maxlength(self, dashboard_server, page):
        """Secret value inputs should have maxlength='1024' attribute."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/onboard?step=5")
        page.wait_for_load_state("networkidle")

        # Check that password inputs (values) have maxlength attribute
        value_inputs = page.locator("input[name='secret_values']")
        count = value_inputs.count()
        assert count >= 1
        assert value_inputs.first.get_attribute("maxlength") == "1024"

    def test_step6_review_loads(self, dashboard_server, page):
        """Step 6 (review) should render without crashing even with empty wizard state."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/onboard?step=6")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert any(word in content.lower() for word in ["review", "create", "identity"])

    def test_step6_review_shows_llm_section(self, dashboard_server, page):
        """Step 6 should show LLM settings section with defaults if not configured."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/onboard?step=6")
        page.wait_for_load_state("networkidle")

        content = page.content()
        # Either shows configured LLM settings or the defaults
        assert "llm" in content.lower() or "model" in content.lower()

    def test_back_navigation_works(self, dashboard_server, page):
        """Back links should navigate to the previous step."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/onboard?step=3")
        page.wait_for_load_state("networkidle")

        # Find the Back link and click it
        back_link = page.locator("a.btn-ghost", has_text="Back")
        assert back_link.count() >= 1
        back_link.first.click()
        page.wait_for_load_state("networkidle")

        # Should navigate to step 2
        assert "step=2" in page.url

    def test_invalid_step_param_handled(self, dashboard_server, page):
        """A non-integer step param should not crash the server."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/onboard?step=abc")
        page.wait_for_load_state("networkidle")

        # Should fall back to step 1 without a 500 error
        assert page.locator("input[name='name']").count() == 1

    def test_wizard_progress_indicator(self, dashboard_server, page):
        """Each step should show a progress indicator."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/onboard?step=1")
        page.wait_for_load_state("networkidle")

        content = page.content()
        # Progress indicator: "Step X of Y" or similar
        assert any(indicator in content for indicator in ["Step", "step", "1 of", "1/"])
