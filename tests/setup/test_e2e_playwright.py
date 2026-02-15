"""
End-to-end Playwright tests for the Överblick Setup Wizard.

Runs a real browser through all 8 wizard steps, takes screenshots
at each step for UI/UX review, and verifies visual elements like
the use-case cards, personality assignment, and form interactions.

Screenshots are saved to tests/setup/screenshots/ for review.

Usage:
    # Run E2E tests (requires playwright install chromium)
    ./venv/bin/python3 -m pytest tests/setup/test_e2e_playwright.py -v

    # Run with headed browser (watch the wizard in action)
    ./venv/bin/python3 -m pytest tests/setup/test_e2e_playwright.py -v --headed
"""

import shutil
import threading
import time
from pathlib import Path

import pytest

# Mark all tests in this module as E2E (slow)
pytestmark = [pytest.mark.e2e]

SCREENSHOT_DIR = Path(__file__).parent / "screenshots"


@pytest.fixture(scope="module")
def screenshot_dir():
    """Create/clean the screenshot directory."""
    if SCREENSHOT_DIR.exists():
        shutil.rmtree(SCREENSHOT_DIR)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR


@pytest.fixture(scope="module")
def setup_server():
    """
    Start the setup wizard server in a background thread (sandbox mode).

    Yields the base URL. Shuts down after all tests in the module.
    """
    import socket

    from overblick.setup.__main__ import _create_sandbox

    # Find a free port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    real_base = Path(__file__).parent.parent.parent
    sandbox_dir = _create_sandbox(real_base)

    from overblick.setup.app import create_setup_app

    app = create_setup_app(base_dir=sandbox_dir)
    url = f"http://127.0.0.1:{port}"

    # Start uvicorn in background thread
    import uvicorn

    config = uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to be ready
    import httpx
    for _ in range(30):
        try:
            resp = httpx.get(url, timeout=1.0)
            if resp.status_code == 200:
                break
        except Exception:
            time.sleep(0.2)
    else:
        raise RuntimeError(f"Setup server did not start at {url}")

    yield url

    # Cleanup
    server.should_exit = True
    thread.join(timeout=5)
    shutil.rmtree(sandbox_dir, ignore_errors=True)


class TestFullWizardFlow:
    """Navigate through all 8 steps and take screenshots."""

    def test_complete_wizard(self, setup_server, screenshot_dir, page):
        """Walk through the entire wizard flow end-to-end."""
        base_url = setup_server

        # -- Step 1: Welcome --
        page.goto(base_url)
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)  # Let CSS animations settle

        assert page.title() == "Welcome — Överblick"
        assert page.locator("h1.setup-title").text_content() == "Överblick"
        assert page.locator(".setup-subtitle").is_visible()
        assert page.locator(".btn-primary.btn-large").is_visible()

        # Check progress bar shows step 1
        active_step = page.locator(".progress-step.active")
        assert active_step.text_content().strip() == "1"

        page.screenshot(path=str(screenshot_dir / "01_welcome.png"), full_page=True)

        # Click "Start Setup"
        page.locator("a.btn-primary.btn-large").click()
        page.wait_for_load_state("networkidle")

        # -- Step 2: Principal Identity --
        assert "Who are you?" in page.content()
        assert page.locator("#principal_name").is_visible()

        # Fill in the form
        page.fill("#principal_name", "Test User")
        page.fill("#principal_email", "test@example.com")

        page.screenshot(path=str(screenshot_dir / "02_principal.png"), full_page=True)

        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # -- Step 3: LLM Configuration --
        assert "AI Engine" in page.title()
        assert page.locator(".radio-card").count() >= 2

        page.screenshot(path=str(screenshot_dir / "03_llm.png"), full_page=True)

        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # -- Step 4: Communication Channels --
        assert "Connect Your Channels" in page.content()
        assert page.locator(".toggle-section").count() >= 2

        page.screenshot(path=str(screenshot_dir / "04_communication.png"), full_page=True)

        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # -- Step 5: Use Cases --
        assert "What Should Your Agents Do?" in page.content()

        # Should have use case cards
        cards = page.locator(".use-case-card")
        assert cards.count() >= 3, f"Expected at least 3 use case cards, got {cards.count()}"

        # Initially no checkboxes should be checked (fresh state)
        counter = page.locator("#selection-count")
        assert counter.text_content().strip() == "0"

        page.screenshot(path=str(screenshot_dir / "05_usecases_initial.png"), full_page=True)

        # Select "Social Media" and "Email Management"
        # force=True needed because checkboxes are visually hidden behind card styling
        page.locator("#uc-social_media").check(force=True)
        time.sleep(0.2)
        page.locator("#uc-email").check(force=True)
        time.sleep(0.2)

        # Counter should update
        assert counter.text_content().strip() == "2"

        page.screenshot(path=str(screenshot_dir / "05_usecases_selected.png"), full_page=True)

        # Submit
        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # -- Step 6: Assign Agents --
        assert "Who Handles What?" in page.content()

        # Should have assignment sections for selected use cases
        sections = page.locator(".assignment-section")
        assert sections.count() == 2, f"Expected 2 assignment sections, got {sections.count()}"

        # Social Media should have personality radio options (multiple choices)
        social_radios = page.locator('input[name="social_media_personality"]')
        assert social_radios.count() >= 3, "Social Media should have multiple personality options"

        # Email should be auto-assigned (only Stal)
        auto_badge = page.locator(".auto-assigned")
        assert auto_badge.count() >= 1, "Email should be auto-assigned"

        page.screenshot(path=str(screenshot_dir / "06_assign_agents.png"), full_page=True)

        # Select Cherry for social media (should be recommended/default)
        cherry_radio = page.locator("#opt-social_media-cherry")
        if not cherry_radio.is_checked():
            cherry_radio.check(force=True)

        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # -- Step 7: Review --
        assert "Review" in page.content()
        assert "Test User" in page.content()

        # Check review sections exist
        assert page.locator(".review-section").count() >= 3

        # Should show agent assignments
        assert "Agent Assignments" in page.content()

        page.screenshot(path=str(screenshot_dir / "07_review.png"), full_page=True)

        # Click "Create Everything"
        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # -- Step 8: Complete --
        assert "ready" in page.content().lower() or "complete" in page.content().lower()
        assert page.locator(".checkmark-circle").is_visible()

        # Wait for confetti animation
        time.sleep(1.0)

        page.screenshot(path=str(screenshot_dir / "08_complete.png"), full_page=True)

        # Check next-steps commands are shown
        assert "python -m overblick" in page.content()


class TestUseCaseSelection:
    """Detailed use case selection interaction tests."""

    def test_use_case_cards_render(self, setup_server, screenshot_dir, page):
        """All use case cards should render with proper content."""
        base_url = setup_server

        _navigate_to_step5(page, base_url)

        cards = page.locator(".use-case-card")
        assert cards.count() >= 4, f"Expected at least 4 use case cards, got {cards.count()}"

        # Check that expected use cases are present
        content = page.content()
        assert "Social Media" in content
        assert "Email Management" in content
        assert "Notifications" in content
        assert "News &amp; Research" in content or "News" in content

        page.screenshot(
            path=str(screenshot_dir / "05_usecase_cards.png"),
            full_page=True,
        )

    def test_checkbox_toggle(self, setup_server, page):
        """Clicking a use case card should toggle the checkbox."""
        base_url = setup_server

        _navigate_to_step5(page, base_url)

        checkbox = page.locator("#uc-research")

        # Ensure unchecked first, then toggle
        checkbox.uncheck(force=True)
        assert not checkbox.is_checked()

        checkbox.check(force=True)
        assert checkbox.is_checked()

        checkbox.uncheck(force=True)
        assert not checkbox.is_checked()

    def test_counter_updates(self, setup_server, page):
        """Selection counter should reflect toggling use case checkboxes."""
        base_url = setup_server

        _navigate_to_step5(page, base_url)

        counter = page.locator("#selection-count")
        label = page.locator("#selection-label")

        # Uncheck all first to get a clean baseline
        for uc_id in ["social_media", "email", "notifications", "research"]:
            page.locator(f"#uc-{uc_id}").uncheck(force=True)
        time.sleep(0.1)
        assert counter.text_content().strip() == "0"

        # Check one
        page.locator("#uc-notifications").check(force=True)
        time.sleep(0.1)
        assert counter.text_content().strip() == "1"
        assert "use case selected" in label.text_content()

        # Check another
        page.locator("#uc-research").check(force=True)
        time.sleep(0.1)
        assert counter.text_content().strip() == "2"
        assert "use cases selected" in label.text_content()

        # Uncheck one
        page.locator("#uc-notifications").uncheck(force=True)
        time.sleep(0.1)
        assert counter.text_content().strip() == "1"

    def test_empty_submission_stays_on_page(self, setup_server, page):
        """Submitting without selecting any use case should stay on step 5."""
        base_url = setup_server

        _navigate_to_step5(page, base_url)

        # Uncheck all to ensure empty submission
        for uc_id in ["social_media", "email", "notifications", "research"]:
            page.locator(f"#uc-{uc_id}").uncheck(force=True)
        time.sleep(0.1)

        # Submit with nothing selected
        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # Should still be on step 5 (not redirected)
        assert "What Should Your Agents Do?" in page.content()

    def test_plugin_badges_visible(self, setup_server, page):
        """Each use case card should show plugin badges."""
        base_url = setup_server

        _navigate_to_step5(page, base_url)

        badges = page.locator(".use-case-plugins .badge")
        assert badges.count() >= 4, "Expected plugin badges on use case cards"


class TestPersonalityAssignment:
    """Test step 6 personality assignment interactions."""

    def test_single_personality_auto_assigned(self, setup_server, screenshot_dir, page):
        """Use cases with one compatible personality should be auto-assigned."""
        base_url = setup_server

        _navigate_to_step6(page, base_url, use_cases=["email"])

        # Email has only Stal — should show auto-assigned badge
        auto_badge = page.locator(".auto-assigned")
        assert auto_badge.count() >= 1, "Email should be auto-assigned to Stal"
        assert "Auto-assigned" in page.content()

        page.screenshot(
            path=str(screenshot_dir / "06_auto_assigned.png"),
            full_page=True,
        )

    def test_multiple_personality_radio_grid(self, setup_server, screenshot_dir, page):
        """Use cases with multiple compatible personalities should show radio options."""
        base_url = setup_server

        _navigate_to_step6(page, base_url, use_cases=["social_media"])

        # Social media has many compatible personalities — should show radio grid
        radios = page.locator('input[name="social_media_personality"]')
        assert radios.count() >= 3, f"Expected 3+ personality options, got {radios.count()}"

        # Should have a recommended badge
        recommended = page.locator(".personality-option .badge-green")
        assert recommended.count() >= 1, "Should show a recommended personality"

        page.screenshot(
            path=str(screenshot_dir / "06_personality_grid.png"),
            full_page=True,
        )

    def test_advanced_settings_toggle(self, setup_server, page):
        """Advanced settings should be collapsed by default and expandable."""
        base_url = setup_server

        _navigate_to_step6(page, base_url, use_cases=["social_media"])

        # Advanced settings should be collapsed (details element)
        details = page.locator(".assignment-config")
        assert details.count() >= 1

        # Body should not be visible initially
        body = page.locator(".assignment-config-body")
        assert not body.is_visible()

        # Click to expand
        page.locator(".assignment-config-summary").first.click()
        time.sleep(0.2)

        # Now body should be visible
        assert body.is_visible()

        # Should have temperature slider, max tokens, heartbeat, quiet hours
        assert page.locator('input[name="social_media_temperature"]').is_visible()
        assert page.locator('input[name="social_media_max_tokens"]').is_visible()
        assert page.locator('input[name="social_media_heartbeat_hours"]').is_visible()
        assert page.locator('input[name="social_media_quiet_hours"]').is_visible()

    def test_submit_redirects_to_review(self, setup_server, page):
        """Submitting step 6 should redirect to step 7 (review)."""
        base_url = setup_server

        _navigate_to_step6(page, base_url, use_cases=["social_media"])

        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        assert "Review" in page.content()

    def test_multiple_use_cases_assignment(self, setup_server, screenshot_dir, page):
        """Multiple use cases should each get their own assignment section."""
        base_url = setup_server

        _navigate_to_step6(page, base_url, use_cases=["social_media", "email", "research"])

        sections = page.locator(".assignment-section")
        assert sections.count() == 3, f"Expected 3 sections, got {sections.count()}"

        page.screenshot(
            path=str(screenshot_dir / "06_multi_assignment.png"),
            full_page=True,
        )


class TestBackNavigation:
    """Test that navigating back preserves state."""

    def test_back_preserves_principal(self, setup_server, page):
        """Going back from step 3 should show step 2 with filled data."""
        base_url = setup_server

        page.goto(base_url)
        page.locator("a.btn-primary.btn-large").click()
        page.wait_for_load_state("networkidle")

        # Fill step 2
        page.fill("#principal_name", "Preserved Name")
        page.fill("#principal_email", "kept@example.com")
        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # Now go back
        page.locator("a.btn-secondary").click()
        page.wait_for_load_state("networkidle")

        # Name should be preserved
        name_val = page.locator("#principal_name").input_value()
        assert name_val == "Preserved Name"

    def test_back_preserves_use_cases(self, setup_server, page):
        """Going back from step 6 to step 5 should preserve selections."""
        base_url = setup_server

        _navigate_to_step6(page, base_url, use_cases=["social_media", "email"])

        # Go back to step 5
        page.locator("a.btn-secondary").click()
        page.wait_for_load_state("networkidle")

        # Use case checkboxes should still be checked
        assert page.locator("#uc-social_media").is_checked()
        assert page.locator("#uc-email").is_checked()


class TestFormValidation:
    """Test form validation in the browser."""

    def test_empty_name_shows_error(self, setup_server, screenshot_dir, page):
        """Submitting step 2 without a name should show error."""
        base_url = setup_server

        page.goto(f"{base_url}/step/2")
        page.wait_for_load_state("networkidle")

        # Clear the name field and submit
        page.fill("#principal_name", "")

        # Remove required attribute to test server-side validation
        page.evaluate("document.getElementById('principal_name').removeAttribute('required')")
        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # Should still be on step 2 (not redirected)
        assert "Who are you?" in page.content()

        page.screenshot(
            path=str(screenshot_dir / "02_validation_error.png"),
            full_page=True,
        )


class TestVisualElements:
    """Test that key visual elements render correctly."""

    def test_progress_bar_updates(self, setup_server, page):
        """Progress bar should highlight the current step."""
        base_url = setup_server

        # Step 1
        page.goto(base_url)
        page.wait_for_load_state("networkidle")
        assert page.locator(".progress-step.active").text_content().strip() == "1"

        # Step 2
        page.goto(f"{base_url}/step/2")
        page.wait_for_load_state("networkidle")
        assert page.locator(".progress-step.active").text_content().strip() == "2"

        # Step 3
        page.goto(f"{base_url}/step/3")
        page.wait_for_load_state("networkidle")
        assert page.locator(".progress-step.active").text_content().strip() == "3"

    def test_logo_renders(self, setup_server, page):
        """Överblick logo should be visible on the welcome page."""
        base_url = setup_server

        page.goto(base_url)
        page.wait_for_load_state("networkidle")

        logo = page.locator(".setup-logo")
        assert logo.is_visible()

    def test_music_controls_present(self, setup_server, page):
        """Music pill should be visible on every page."""
        base_url = setup_server

        page.goto(base_url)
        page.wait_for_load_state("networkidle")

        pill = page.locator(".music-pill")
        assert pill.is_visible()

        toggle = page.locator("#music-toggle")
        assert toggle.is_visible()

        volume = page.locator("#music-volume")
        assert volume.is_visible()

    def test_dark_theme_applied(self, setup_server, page):
        """Body should have dark background color."""
        base_url = setup_server

        page.goto(base_url)
        page.wait_for_load_state("networkidle")

        bg_color = page.evaluate(
            "getComputedStyle(document.body).backgroundColor"
        )
        # Dark theme — RGB should be low values
        # bg-primary is #0d1117 -> rgb(13, 17, 23)
        assert "rgb(" in bg_color
        values = bg_color.replace("rgb(", "").replace(")", "").split(",")
        r, g, b = [int(v.strip()) for v in values]
        assert r < 50 and g < 50 and b < 50, f"Not dark theme: {bg_color}"


class TestResponsiveness:
    """Test mobile viewport rendering."""

    def test_mobile_viewport(self, setup_server, screenshot_dir, page):
        """Wizard should render well on mobile viewport."""
        base_url = setup_server

        page.set_viewport_size({"width": 375, "height": 812})  # iPhone X
        page.goto(base_url)
        page.wait_for_load_state("networkidle")
        time.sleep(0.5)

        page.screenshot(
            path=str(screenshot_dir / "01_welcome_mobile.png"),
            full_page=True,
        )

        # Navigate to use case select
        _navigate_to_step5(page, base_url)

        # Use case grid should switch to single column on mobile
        cards = page.locator(".use-case-card")
        assert cards.count() >= 3

        page.screenshot(
            path=str(screenshot_dir / "05_usecases_mobile.png"),
            full_page=True,
        )

    def test_mobile_assignment_step(self, setup_server, screenshot_dir, page):
        """Assignment step should render well on mobile viewport."""
        base_url = setup_server

        page.set_viewport_size({"width": 375, "height": 812})

        _navigate_to_step6(page, base_url, use_cases=["social_media"])

        # Personality grid should wrap on mobile
        grid = page.locator(".personality-grid")
        assert grid.count() >= 1

        page.screenshot(
            path=str(screenshot_dir / "06_assign_mobile.png"),
            full_page=True,
        )


# -- Helpers --


def _navigate_to_step5(page, base_url: str) -> None:
    """Navigate through steps 1-4 to reach step 5 (use cases)."""
    page.goto(base_url)
    page.locator("a.btn-primary.btn-large").click()
    page.wait_for_load_state("networkidle")

    # Step 2: fill principal
    page.fill("#principal_name", "E2E Test")
    page.locator("button[type='submit']").click()
    page.wait_for_load_state("networkidle")

    # Step 3: defaults
    page.locator("button[type='submit']").click()
    page.wait_for_load_state("networkidle")

    # Step 4: skip
    page.locator("button[type='submit']").click()
    page.wait_for_load_state("networkidle")


def _navigate_to_step6(
    page, base_url: str, use_cases: list[str] | None = None,
) -> None:
    """Navigate through steps 1-5 to reach step 6 (assign agents)."""
    _navigate_to_step5(page, base_url)

    # Step 5: uncheck all first (server state may persist from previous tests)
    all_uc_ids = ["social_media", "email", "notifications", "research"]
    for uc_id in all_uc_ids:
        page.locator(f"#uc-{uc_id}").uncheck(force=True)

    # Select only the specified use cases
    if use_cases is None:
        use_cases = ["social_media"]

    for uc_id in use_cases:
        page.locator(f"#uc-{uc_id}").check(force=True)
        time.sleep(0.1)

    page.locator("button[type='submit']").click()
    page.wait_for_load_state("networkidle")
