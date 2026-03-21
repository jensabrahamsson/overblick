"""
E2E tests for the backend preference UI in step 3 of the settings wizard.

Covers:
- Preference list rendering based on enabled backends
- Up/down arrow reordering
- Dynamic list updates when backends are toggled
- Hidden input value correctness
- Remote backend (renamed from cloud)
- Review page shows preference order
"""

import pytest

pytestmark = [pytest.mark.e2e]


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


def _goto_step3(page, base_url: str):
    """Navigate to step 3 (LLM backends) with login."""
    _login(page, base_url)
    page.goto(f"{base_url}/settings/step/3")
    page.wait_for_load_state("networkidle")


class TestStep3BackendPanels:
    """Verify all backend panels render correctly."""

    def test_local_backend_panel_present(self, dashboard_server, page):
        _goto_step3(page, dashboard_server)
        assert page.locator("input[name='local_enabled']").count() == 1
        assert page.locator("#local-config").count() == 1

    def test_remote_backend_panel_present(self, dashboard_server, page):
        """Remote backend (renamed from cloud) should exist."""
        _goto_step3(page, dashboard_server)
        assert page.locator("input[name='remote_enabled']").count() == 1
        assert page.locator("#remote-config").count() == 1

    def test_deepseek_backend_panel_present(self, dashboard_server, page):
        _goto_step3(page, dashboard_server)
        assert page.locator("input[name='deepseek_enabled']").count() == 1
        assert page.locator("#deepseek-config").count() == 1

    def test_no_cloud_backend_panel(self, dashboard_server, page):
        """Old 'cloud' backend should no longer exist."""
        _goto_step3(page, dashboard_server)
        assert page.locator("input[name='cloud_enabled']").count() == 0

    def test_remote_panel_text(self, dashboard_server, page):
        """Remote panel should say 'Remote Inference'."""
        _goto_step3(page, dashboard_server)
        content = page.content()
        assert "Remote Inference" in content


class TestPreferenceList:
    """Test the routing priority list UI."""

    def test_preference_list_exists(self, dashboard_server, page):
        """Preference list element should render."""
        _goto_step3(page, dashboard_server)
        assert page.locator("#preference-list").count() == 1

    def test_hidden_input_exists(self, dashboard_server, page):
        """Hidden input for backend_preference should exist."""
        _goto_step3(page, dashboard_server)
        hidden = page.locator("input[name='backend_preference']")
        assert hidden.count() == 1

    def test_local_only_shows_one_item(self, dashboard_server, page):
        """With only local enabled, preference list has one item."""
        _goto_step3(page, dashboard_server)

        local_cb = page.locator("input[name='local_enabled']")
        if not local_cb.is_checked():
            local_cb.check()
        for name in ["remote_enabled", "deepseek_enabled"]:
            cb = page.locator(f"input[name='{name}']")
            if cb.is_checked():
                cb.uncheck()

        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        assert items.count() == 1
        assert items.first.get_attribute("data-backend") == "local"

    def test_enabling_remote_adds_to_list(self, dashboard_server, page):
        """Enabling remote should add it to the preference list."""
        _goto_step3(page, dashboard_server)

        local_cb = page.locator("input[name='local_enabled']")
        if not local_cb.is_checked():
            local_cb.check()
        for name in ["remote_enabled", "deepseek_enabled"]:
            cb = page.locator(f"input[name='{name}']")
            if cb.is_checked():
                cb.uncheck()
        page.wait_for_timeout(200)

        page.locator("input[name='remote_enabled']").check()
        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        assert items.count() == 2

        backends = [
            items.nth(i).get_attribute("data-backend") for i in range(items.count())
        ]
        assert "remote" in backends
        assert "local" in backends

    def test_enabling_all_shows_three_items(self, dashboard_server, page):
        """Enabling all 3 backends should show 3 preference items."""
        _goto_step3(page, dashboard_server)

        for name in ["local_enabled", "remote_enabled", "deepseek_enabled"]:
            cb = page.locator(f"input[name='{name}']")
            if not cb.is_checked():
                cb.check()
        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        assert items.count() == 3

    def test_disabling_backend_removes_from_list(self, dashboard_server, page):
        """Disabling a backend should remove it from the preference list."""
        _goto_step3(page, dashboard_server)

        for name in ["local_enabled", "remote_enabled", "deepseek_enabled"]:
            cb = page.locator(f"input[name='{name}']")
            if not cb.is_checked():
                cb.check()
        page.wait_for_timeout(200)

        page.locator("input[name='deepseek_enabled']").uncheck()
        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        assert items.count() == 2
        backends = [
            items.nth(i).get_attribute("data-backend") for i in range(items.count())
        ]
        assert "deepseek" not in backends

    def test_hidden_input_updates_on_toggle(self, dashboard_server, page):
        """Hidden input value should update when backends are toggled."""
        _goto_step3(page, dashboard_server)

        page.locator("input[name='local_enabled']").check()
        page.locator("input[name='remote_enabled']").check()
        cb = page.locator("input[name='deepseek_enabled']")
        if cb.is_checked():
            cb.uncheck()
        page.wait_for_timeout(200)

        value = page.locator("input[name='backend_preference']").input_value()
        parts = [p for p in value.split(",") if p]
        assert "local" in parts
        assert "remote" in parts
        assert len(parts) == 2


class TestPreferenceReordering:
    """Test up/down arrow reordering in the preference list."""

    def test_move_down_button_works(self, dashboard_server, page):
        """Clicking down arrow on first item should move it down."""
        _goto_step3(page, dashboard_server)

        page.locator("input[name='local_enabled']").check()
        page.locator("input[name='remote_enabled']").check()
        cb = page.locator("input[name='deepseek_enabled']")
        if cb.is_checked():
            cb.uncheck()
        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        first_backend = items.first.get_attribute("data-backend")

        items.first.locator(".pref-down").click()
        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        assert items.nth(1).get_attribute("data-backend") == first_backend

    def test_move_up_button_works(self, dashboard_server, page):
        """Clicking up arrow on second item should move it up."""
        _goto_step3(page, dashboard_server)

        page.locator("input[name='local_enabled']").check()
        page.locator("input[name='remote_enabled']").check()
        cb = page.locator("input[name='deepseek_enabled']")
        if cb.is_checked():
            cb.uncheck()
        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        second_backend = items.nth(1).get_attribute("data-backend")

        items.nth(1).locator(".pref-up").click()
        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        assert items.first.get_attribute("data-backend") == second_backend

    def test_rank_numbers_update_after_reorder(self, dashboard_server, page):
        """Rank numbers (1, 2, ...) should update after reorder."""
        _goto_step3(page, dashboard_server)

        page.locator("input[name='local_enabled']").check()
        page.locator("input[name='remote_enabled']").check()
        cb = page.locator("input[name='deepseek_enabled']")
        if cb.is_checked():
            cb.uncheck()
        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        items.first.locator(".pref-down").click()
        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        rank1 = items.nth(0).locator(".preference-rank").text_content()
        rank2 = items.nth(1).locator(".preference-rank").text_content()
        assert rank1.strip() == "1"
        assert rank2.strip() == "2"

    def test_hidden_input_updates_after_reorder(self, dashboard_server, page):
        """Hidden input should reflect the new order after reordering."""
        _goto_step3(page, dashboard_server)

        for name in ["local_enabled", "remote_enabled", "deepseek_enabled"]:
            page.locator(f"input[name='{name}']").check()
        page.wait_for_timeout(200)

        value_before = page.locator("input[name='backend_preference']").input_value()

        items = page.locator("#preference-list .preference-item[data-backend]")
        items.first.locator(".pref-down").click()
        page.wait_for_timeout(200)

        value_after = page.locator("input[name='backend_preference']").input_value()
        assert value_before != value_after

    def test_first_item_up_button_disabled(self, dashboard_server, page):
        """First item's up button should be disabled."""
        _goto_step3(page, dashboard_server)

        page.locator("input[name='local_enabled']").check()
        page.locator("input[name='remote_enabled']").check()
        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        assert items.first.locator(".pref-up").is_disabled()

    def test_last_item_down_button_disabled(self, dashboard_server, page):
        """Last item's down button should be disabled."""
        _goto_step3(page, dashboard_server)

        page.locator("input[name='local_enabled']").check()
        page.locator("input[name='remote_enabled']").check()
        page.wait_for_timeout(200)

        items = page.locator("#preference-list .preference-item[data-backend]")
        assert items.last.locator(".pref-down").is_disabled()


class TestStep3FormElements:
    """Test form elements for correctness."""

    def test_default_backend_dropdown_has_remote(self, dashboard_server, page):
        """Default backend dropdown should have 'remote' option."""
        _goto_step3(page, dashboard_server)

        select = page.locator("select[name='default_backend']")
        options = select.locator("option")
        values = [options.nth(i).get_attribute("value") for i in range(options.count())]
        assert "remote" in values
        assert "local" in values
        assert "deepseek" in values

    def test_default_backend_dropdown_no_cloud(self, dashboard_server, page):
        """Default backend dropdown should NOT have old 'cloud' option."""
        _goto_step3(page, dashboard_server)

        select = page.locator("select[name='default_backend']")
        options = select.locator("option")
        values = [options.nth(i).get_attribute("value") for i in range(options.count())]
        assert "cloud" not in values
        assert "dashscope" not in values


class TestBackendToggleUI:
    """Test backend config body show/hide behavior."""

    def test_remote_config_hidden_when_unchecked(self, dashboard_server, page):
        _goto_step3(page, dashboard_server)

        cb = page.locator("input[name='remote_enabled']")
        if cb.is_checked():
            cb.uncheck()
        page.wait_for_timeout(100)

        config = page.locator("#remote-config")
        assert config.is_hidden()

    def test_remote_config_shown_when_checked(self, dashboard_server, page):
        _goto_step3(page, dashboard_server)

        cb = page.locator("input[name='remote_enabled']")
        if not cb.is_checked():
            cb.check()
        page.wait_for_timeout(100)

        config = page.locator("#remote-config")
        assert config.is_visible()


class TestRoutingPrioritySection:
    """Test the Routing Priority section heading and description."""

    def test_routing_priority_heading(self, dashboard_server, page):
        _goto_step3(page, dashboard_server)
        content = page.content()
        assert "Routing Priority" in content

    def test_routing_priority_description(self, dashboard_server, page):
        _goto_step3(page, dashboard_server)
        content = page.content()
        assert "fallback order" in content.lower() or "preference" in content.lower()
