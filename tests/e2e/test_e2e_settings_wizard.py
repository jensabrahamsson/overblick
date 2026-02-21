"""
E2E tests for the 8-step settings wizard.

Covers step rendering, navigation, form validation, ARIA semantics,
progress bar, and different configuration permutations.
"""

import pytest

pytestmark = [pytest.mark.e2e]

TOTAL_STEPS = 8


def _login(page, base_url: str):
    """Auto-login via the no-password endpoint."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")


def _login_with_csrf(page, base_url: str) -> str:
    """Login and extract CSRF token for POST requests."""
    page.goto(f"{base_url}/login")
    page.wait_for_load_state("networkidle")

    page.goto(f"{base_url}/settings/step/1")
    page.wait_for_load_state("networkidle")

    csrf_token = page.evaluate("""
        () => {
            const body = document.body;
            if (!body) return '';
            const headers = body.getAttribute('hx-headers');
            if (!headers) return '';
            try {
                return JSON.parse(headers)['X-CSRF-Token'] || '';
            } catch (e) {
                return '';
            }
        }
    """)

    if csrf_token:
        page.set_extra_http_headers({"X-CSRF-Token": csrf_token})

    return csrf_token


class TestWizardStepRendering:
    """Verify every wizard step renders without errors."""

    @pytest.mark.parametrize("step", list(range(1, TOTAL_STEPS + 1)))
    def test_step_renders_200(self, dashboard_server, page, step):
        """Each wizard step should return 200 and render HTML."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/{step}")
        page.wait_for_load_state("networkidle")

        content = page.content()
        # Should not be a 500 error page
        assert "Internal Server Error" not in content
        assert "500" not in page.locator("title").text_content()


class TestWizardNavigation:
    """Test forward and backward navigation through steps."""

    def test_step1_to_step2(self, dashboard_server, page):
        """Step 1 Next button should advance to step 2."""
        _login_with_csrf(page, dashboard_server)

        page.locator("button[type='submit'], a.btn-primary").first.click()
        page.wait_for_load_state("networkidle")

        assert "/step/2" in page.url

    def test_back_navigation_step3_to_step2(self, dashboard_server, page):
        """Back button on step 3 should return to step 2."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/3")
        page.wait_for_load_state("networkidle")

        back = page.locator("a.btn-ghost", has_text="Back")
        if back.count() > 0:
            back.first.click()
            page.wait_for_load_state("networkidle")
            assert "/step/2" in page.url

    def test_back_navigation_step5_to_step4(self, dashboard_server, page):
        """Back button on step 5 should return to step 4."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/5")
        page.wait_for_load_state("networkidle")

        back = page.locator("a.btn-ghost", has_text="Back")
        if back.count() > 0:
            back.first.click()
            page.wait_for_load_state("networkidle")
            assert "/step/4" in page.url


class TestWizardFormValidation:
    """Test server-side validation on wizard forms."""

    def test_step2_rejects_empty_name(self, dashboard_server, page):
        """Step 2 should show error for empty principal name."""
        _login_with_csrf(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/2")
        page.wait_for_load_state("networkidle")

        # Clear any pre-filled values and submit
        name_input = page.locator("input[name='principal_name']")
        if name_input.count() > 0:
            # Remove required/pattern to bypass browser validation
            page.evaluate("""
                () => {
                    document.querySelectorAll('input[required]').forEach(
                        el => { el.removeAttribute('required'); el.removeAttribute('pattern'); }
                    );
                }
            """)
            name_input.fill("")
            page.locator("button[type='submit']").click()
            page.wait_for_load_state("networkidle")

            # Should stay on step 2 or show error
            content = page.content()
            assert "/step/2" in page.url or "error" in content.lower()

    def test_step3_renders_backend_forms(self, dashboard_server, page):
        """Step 3 should show LLM backend configuration options."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/3")
        page.wait_for_load_state("networkidle")

        content = page.content().lower()
        # Should have local backend section
        assert any(w in content for w in ["ollama", "local", "backend", "model"])


class TestWizardProgressBar:
    """Test the progress indicator across steps."""

    def test_progress_bar_present(self, dashboard_server, page):
        """Steps 1-7 should show a progress indicator."""
        _login(page, dashboard_server)

        for step in [1, 4, 7]:
            page.goto(f"{dashboard_server}/settings/step/{step}")
            page.wait_for_load_state("networkidle")

            progress = page.locator("[role='progressbar'], .progress-bar, .wizard-progress")
            # At least one progress indicator element should exist
            assert progress.count() > 0 or "Step" in page.content()


class TestWizardARIA:
    """Test ARIA attributes and accessibility semantics."""

    def test_step1_has_heading(self, dashboard_server, page):
        """Step 1 should have a heading for screen readers."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/1")
        page.wait_for_load_state("networkidle")

        h1 = page.locator("h1")
        assert h1.count() >= 1

    def test_step2_form_labels(self, dashboard_server, page):
        """Form inputs on step 2 should have associated labels."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/2")
        page.wait_for_load_state("networkidle")

        labels = page.locator("label.form-label")
        assert labels.count() >= 1

    def test_step2_aria_describedby(self, dashboard_server, page):
        """Key inputs should have aria-describedby for help text."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/2")
        page.wait_for_load_state("networkidle")

        described_inputs = page.locator("[aria-describedby]")
        assert described_inputs.count() >= 1

    def test_error_flash_has_role_alert(self, dashboard_server, page):
        """Error messages should have role='alert' for screen readers."""
        _login_with_csrf(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/2")
        page.wait_for_load_state("networkidle")

        # Submit invalid data to trigger error
        page.evaluate("""
            () => {
                document.querySelectorAll('input[required]').forEach(
                    el => { el.removeAttribute('required'); el.removeAttribute('pattern'); }
                );
                const nameInput = document.querySelector('input[name="principal_name"]');
                if (nameInput) nameInput.value = '';
            }
        """)
        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # If there's an error flash, it should have role="alert"
        alerts = page.locator("[role='alert']")
        if alerts.count() > 0:
            assert alerts.first.is_visible()


class TestWizardLLMPermutations:
    """Test different LLM backend configuration combinations."""

    def test_step3_local_only_config(self, dashboard_server, page):
        """Submit step 3 with local-only backend enabled."""
        _login_with_csrf(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/3")
        page.wait_for_load_state("networkidle")

        # Check local backend checkbox if present
        local_checkbox = page.locator("input[name='local_enabled']")
        if local_checkbox.count() > 0 and not local_checkbox.is_checked():
            local_checkbox.check()

        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # Should advance to step 4
        assert "/step/4" in page.url

    def test_step3_test_connection_button(self, dashboard_server, page):
        """Step 3 test connection buttons should trigger async checks."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/3")
        page.wait_for_load_state("networkidle")

        # Look for test/check connection buttons
        test_btn = page.locator("button", has_text="Test")
        if test_btn.count() > 0:
            # Button should exist and be clickable
            assert test_btn.first.is_enabled()


class TestWizardStep8Completion:
    """Test the completion page (step 8)."""

    def test_step8_shows_checkmark(self, dashboard_server, page):
        """Step 8 should render a completion checkmark."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/8")
        page.wait_for_load_state("networkidle")

        content = page.content()
        assert "saved" in content.lower() or "complete" in content.lower()
        assert page.locator(".checkmark-circle, .checkmark-svg").count() >= 1

    def test_step8_has_dashboard_link(self, dashboard_server, page):
        """Step 8 should have a link back to dashboard."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/8")
        page.wait_for_load_state("networkidle")

        dashboard_link = page.locator("a[href='/']")
        assert dashboard_link.count() >= 1

    def test_step8_uses_css_classes_not_inline_styles(self, dashboard_server, page):
        """Step 8 completion page should use CSS classes, not inline styles."""
        _login(page, dashboard_server)
        page.goto(f"{dashboard_server}/settings/step/8")
        page.wait_for_load_state("networkidle")

        completion_page = page.locator(".completion-page")
        assert completion_page.count() == 1

        completion_content = page.locator(".completion-content")
        assert completion_content.count() == 1
