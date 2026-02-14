"""
Playwright test to inspect dashboard plugin cards.
"""

import pytest
from playwright.sync_api import sync_playwright, expect


def test_dashboard_plugin_cards():
    """
    Test that plugin cards are rendered correctly.

    DEBUG: This test will show what's actually returned from the dashboard.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Navigate to dashboard
        try:
            page.goto("http://localhost:8080/", wait_until="networkidle", timeout=5000)
        except Exception as e:
            pytest.skip(f"Dashboard not running at localhost:8080: {e}")

        # Wait for page to load
        page.wait_for_load_state("networkidle")

        # Take a screenshot for debugging
        page.screenshot(path="/tmp/dashboard_debug.png")
        print("\n📸 Screenshot saved to /tmp/dashboard_debug.png")

        # Get the full HTML
        html = page.content()

        # Check system health
        health_section = page.locator(".health-bar")
        if health_section.count() > 0:
            health_text = health_section.text_content()
            print(f"\n🏥 System Health:\n{health_text}")

        # Check plugin section
        plugin_section = page.locator(".section:has(h2:text('Plugins'))")
        if plugin_section.count() > 0:
            plugin_text = plugin_section.text_content()
            print(f"\n🔌 Plugins Section:\n{plugin_text}")

        # Check for empty state
        empty_state = page.locator(".empty-state")
        if empty_state.count() > 0:
            print(f"\n❌ EMPTY STATE DETECTED: {empty_state.count()} instances")

        # Check for plugin cards
        plugin_cards = page.locator(".agent-card")
        card_count = plugin_cards.count()
        print(f"\n📊 Plugin Cards Found: {card_count}")

        if card_count > 0:
            for i in range(card_count):
                card = plugin_cards.nth(i)
                card_name = card.locator(".agent-card-name").text_content()
                card_desc = card.locator(".agent-card-desc").text_content()
                print(f"  Card {i+1}: {card_name}")
                print(f"    {card_desc}")

        # Now fetch the partial directly
        print("\n🔍 Fetching /partials/plugin-cards directly...")
        response = page.goto("http://localhost:8080/partials/plugin-cards")
        partial_html = page.content()

        print(f"Partial HTML length: {len(partial_html)} chars")
        if "empty-state" in partial_html:
            print("❌ Partial contains empty-state!")
        elif "agent-card" in partial_html:
            print("✅ Partial contains agent-card!")
        else:
            print("⚠️  Partial contains neither empty-state nor agent-card")

        # Print first 500 chars of partial
        print(f"\nFirst 500 chars of partial:\n{partial_html[:500]}")

        browser.close()


if __name__ == "__main__":
    test_dashboard_plugin_cards()
