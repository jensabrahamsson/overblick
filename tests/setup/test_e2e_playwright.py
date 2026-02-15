"""
End-to-end Playwright tests for the Överblick Setup Wizard.

Runs a real browser through all 8 wizard steps, takes screenshots
at each step for UI/UX review, and verifies visual elements like
the character carousel, animations, and form interactions.

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

        # ── Step 1: Welcome ──
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

        # ── Step 2: Principal Identity ──
        assert "Who are you?" in page.content()
        assert page.locator("#principal_name").is_visible()

        # Fill in the form
        page.fill("#principal_name", "Test User")
        page.fill("#principal_email", "test@example.com")
        # Timezone defaults to Europe/Stockholm
        # Language defaults to English

        page.screenshot(path=str(screenshot_dir / "02_principal.png"), full_page=True)

        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # ── Step 3: LLM Configuration ──
        assert "AI Engine" in page.title()
        assert page.locator(".radio-card").count() >= 2

        # Defaults are fine (Ollama, qwen3:8b)
        page.screenshot(path=str(screenshot_dir / "03_llm.png"), full_page=True)

        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # ── Step 4: Communication Channels ──
        assert "Connect Your Channels" in page.content()
        assert page.locator(".toggle-section").count() >= 2

        # Skip Gmail and Telegram (both optional)
        page.screenshot(path=str(screenshot_dir / "04_communication.png"), full_page=True)

        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # ── Step 5: Character Select ──
        assert "Choose Your Agents" in page.content()

        # Wait for JS carousel to render
        page.wait_for_selector(".character-card", timeout=5000)
        time.sleep(0.5)  # Let CSS transitions settle

        # Should have multiple character cards
        cards = page.locator(".character-card")
        assert cards.count() >= 3, f"Expected at least 3 character cards, got {cards.count()}"

        # One card should be focused
        focused = page.locator(".character-card.focused")
        assert focused.count() == 1

        page.screenshot(path=str(screenshot_dir / "05_characters_initial.png"), full_page=True)

        # Test carousel keyboard navigation
        page.keyboard.press("ArrowRight")
        time.sleep(0.3)
        page.keyboard.press("ArrowRight")
        time.sleep(0.3)

        page.screenshot(path=str(screenshot_dir / "05_characters_nav_right.png"), full_page=True)

        # Navigate back left
        page.keyboard.press("ArrowLeft")
        time.sleep(0.3)

        # Select the focused character with Space
        page.keyboard.press("Space")
        time.sleep(0.3)

        # Should now have a selected card
        selected_cards = page.locator(".character-card.selected")
        assert selected_cards.count() >= 1, "No character was selected"

        # Selection counter should update
        counter = page.locator("#selection-counter")
        assert "1" in counter.text_content()

        # Navigate right and select another
        page.keyboard.press("ArrowRight")
        time.sleep(0.3)
        page.keyboard.press("Space")
        time.sleep(0.3)

        selected_cards = page.locator(".character-card.selected")
        assert selected_cards.count() >= 2, "Second character was not selected"

        page.screenshot(path=str(screenshot_dir / "05_characters_selected.png"), full_page=True)

        # Submit
        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # ── Step 6: Agent Configuration ──
        assert "Configure Your Agents" in page.content()

        # Should have config for selected agents
        page.screenshot(path=str(screenshot_dir / "06_agent_config.png"), full_page=True)

        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # ── Step 7: Review ──
        assert "Review" in page.content()
        assert "Test User" in page.content()

        # Check review sections exist
        assert page.locator(".review-section").count() >= 3

        page.screenshot(path=str(screenshot_dir / "07_review.png"), full_page=True)

        # Click "Create Everything"
        page.locator("button[type='submit']").click()
        page.wait_for_load_state("networkidle")

        # ── Step 8: Complete ──
        assert "ready" in page.content().lower() or "complete" in page.content().lower()
        assert page.locator(".checkmark-circle").is_visible()

        # Wait for confetti animation
        time.sleep(1.0)

        page.screenshot(path=str(screenshot_dir / "08_complete.png"), full_page=True)

        # Check next-steps commands are shown
        assert "python -m overblick" in page.content()


class TestCharacterCarousel:
    """Detailed carousel interaction tests."""

    def test_carousel_click_navigation(self, setup_server, screenshot_dir, page):
        """Test clicking on side characters to focus them."""
        base_url = setup_server

        # Navigate to step 5 by walking through the wizard
        _navigate_to_step5(page, base_url)

        page.wait_for_selector(".character-card", timeout=5000)
        time.sleep(0.5)

        cards = page.locator(".character-card")
        count = cards.count()

        if count >= 3:
            # Click on a non-focused card
            focused_idx = None
            for i in range(count):
                if "focused" in (cards.nth(i).get_attribute("class") or ""):
                    focused_idx = i
                    break

            # Click the next card
            click_idx = (focused_idx + 1) % count if focused_idx is not None else 1
            cards.nth(click_idx).click()
            time.sleep(0.3)

            # That card should now be focused
            new_focused = page.locator(".character-card.focused")
            assert new_focused.count() == 1

            page.screenshot(
                path=str(screenshot_dir / "05_carousel_click_nav.png"),
                full_page=True,
            )

    def test_select_button_click(self, setup_server, screenshot_dir, page):
        """Test clicking the select button on a character card."""
        base_url = setup_server

        _navigate_to_step5(page, base_url)

        page.wait_for_selector(".character-card.focused", timeout=5000)
        time.sleep(0.5)

        # Click the select button on the focused card
        btn = page.locator(".character-card.focused .character-select-btn")
        assert btn.is_visible()
        btn.click()
        time.sleep(0.3)

        # Button should now show "Selected"
        assert "Selected" in btn.text_content()

        # Card should have .selected class
        assert page.locator(".character-card.selected").count() >= 1

        page.screenshot(
            path=str(screenshot_dir / "05_carousel_select_btn.png"),
            full_page=True,
        )

    def test_trait_bars_visible_on_focus(self, setup_server, page):
        """Focused card should show trait bars."""
        base_url = setup_server

        _navigate_to_step5(page, base_url)

        page.wait_for_selector(".character-card.focused", timeout=5000)
        time.sleep(0.5)

        # Focused card should have trait bars
        focused = page.locator(".character-card.focused")
        trait_bars = focused.locator(".trait-bar")
        assert trait_bars.count() >= 1, "No trait bars on focused card"


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


class TestFormValidation:
    """Test form validation in the browser."""

    def test_empty_name_shows_error(self, setup_server, screenshot_dir, page):
        """Submitting step 2 without a name should show error."""
        base_url = setup_server

        page.goto(f"{base_url}/step/2")
        page.wait_for_load_state("networkidle")

        # Clear the name field and submit
        page.fill("#principal_name", "")

        # The HTML5 required attribute may prevent submission via button click,
        # so we need to remove it first to test server-side validation
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
        # bg-primary is #0d1117 → rgb(13, 17, 23)
        assert "rgb(" in bg_color
        # Extract values
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

        # Navigate to character select
        _navigate_to_step5(page, base_url)
        page.wait_for_selector(".character-card", timeout=5000)
        time.sleep(0.5)

        page.screenshot(
            path=str(screenshot_dir / "05_characters_mobile.png"),
            full_page=True,
        )


# ── Helpers ──


def _navigate_to_step5(page, base_url: str) -> None:
    """Navigate through steps 1-4 to reach step 5 (character select)."""
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
