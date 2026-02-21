"""
Tests for the integrated settings wizard at /settings/.

Tests that:
- All 8 GET endpoints render 200 and return HTML
- POST steps validate input and redirect to the next step
- Step 7 POST triggers provisioning and redirects to step 8
- Invalid form data renders the step again with an error
- New backends-format LLM config works correctly
"""

import pytest
from unittest.mock import patch

from overblick.dashboard.auth import SESSION_COOKIE


class TestSettingsStep1:
    @pytest.mark.asyncio
    async def test_settings_root_redirects(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/",
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/settings/step/1"

    @pytest.mark.asyncio
    async def test_step1_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/1",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Set Up" in resp.text or "Reconfigure" in resp.text or "verblick" in resp.text

    @pytest.mark.asyncio
    async def test_step1_post_redirects_to_step2(self, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/1",
            headers={"X-CSRF-Token": csrf_token},
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/step/2" in resp.headers["location"]


class TestSettingsStep2:
    @pytest.mark.asyncio
    async def test_step2_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/2",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        # Should contain principal-related content
        assert "name" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_step2_valid_post_redirects(self, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/2",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "principal_name": "Alice Andersson",
                "principal_email": "alice@example.com",
                "timezone": "Europe/Stockholm",
                "language_preference": "en",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/step/3" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_step2_invalid_name_shows_error(self, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/2",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "principal_name": "",
                "principal_email": "",
                "timezone": "Europe/Stockholm",
                "language_preference": "en",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "error" in resp.text.lower() or "required" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_step2_invalid_email_shows_error(self, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/2",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "principal_name": "Alice",
                "principal_email": "not-an-email",
                "timezone": "Europe/Stockholm",
                "language_preference": "en",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "Invalid" in resp.text or "email" in resp.text.lower()


class TestSettingsStep3:
    @pytest.mark.asyncio
    async def test_step3_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/3",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Gateway" in resp.text or "gateway" in resp.text
        assert "Local" in resp.text or "local" in resp.text

    @pytest.mark.asyncio
    async def test_step3_backends_post_redirects(self, client, session_cookie):
        """Test Ollama provider POST with UI field names."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/3",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "llm_provider": "ollama",
                "ollama_host": "127.0.0.1",
                "ollama_port": "11434",
                "model": "qwen3:8b",
                "gateway_url": "http://127.0.0.1:8200",
                "default_temperature": "0.7",
                "default_max_tokens": "2000",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/step/4" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_step3_cloud_backend_post(self, client, session_cookie):
        """Test gateway provider POST."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/3",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "llm_provider": "gateway",
                "ollama_host": "127.0.0.1",
                "ollama_port": "11434",
                "model": "qwen3:8b",
                "gateway_url": "http://127.0.0.1:8200",
                "default_temperature": "0.8",
                "default_max_tokens": "4000",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/step/4" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_step3_invalid_port_shows_error(self, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/3",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "llm_provider": "ollama",
                "ollama_host": "127.0.0.1",
                "ollama_port": "not_a_number",
                "model": "qwen3:8b",
                "default_temperature": "0.7",
                "default_max_tokens": "2000",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 200


class TestSettingsStep4:
    @pytest.mark.asyncio
    async def test_step4_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/4",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Gmail" in resp.text or "gmail" in resp.text
        assert "Telegram" in resp.text or "telegram" in resp.text

    @pytest.mark.asyncio
    async def test_step4_skip_channels_redirects(self, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/4",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "gmail_enabled": "off",
                "telegram_enabled": "off",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/step/5" in resp.headers["location"]


class TestSettingsStep5:
    @pytest.mark.asyncio
    async def test_step5_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/5",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Social Media" in resp.text

    @pytest.mark.asyncio
    async def test_step5_valid_selection_redirects(self, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/5",
            headers={"X-CSRF-Token": csrf_token},
            data={"selected_use_cases": "social_media"},
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/step/6" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_step5_empty_selection_shows_error(self, client, session_cookie):
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/5",
            headers={"X-CSRF-Token": csrf_token},
            data={},
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "least one" in resp.text or "select" in resp.text.lower()


class TestSettingsStep6:
    @pytest.mark.asyncio
    async def test_step6_renders(self, client, session_cookie, app):
        from overblick.setup.wizard import _get_state
        state = _get_state(app)
        state["selected_use_cases"] = ["social_media"]

        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/6",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_step6_post_redirects_to_review(self, client, session_cookie, app):
        from overblick.setup.wizard import _get_state
        state = _get_state(app)
        state["selected_use_cases"] = ["social_media"]

        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/step/6",
            headers={"X-CSRF-Token": csrf_token},
            data={
                "social_media_personality": "cherry",
                "social_media_temperature": "0.8",
                "social_media_max_tokens": "2000",
                "social_media_heartbeat_hours": "4",
                "social_media_quiet_hours": "on",
            },
            cookies={SESSION_COOKIE: cookie_value},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/step/7" in resp.headers["location"]


class TestSettingsStep7:
    @pytest.mark.asyncio
    async def test_step7_renders(self, client, session_cookie, app):
        from overblick.setup.wizard import _get_state
        state = _get_state(app)
        state["selected_use_cases"] = ["social_media"]
        state["assignments"] = {
            "social_media": {"personality": "cherry", "temperature": 0.8,
                             "max_tokens": 2000, "heartbeat_hours": 4, "quiet_hours": True},
        }

        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/7",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Review" in resp.text

    @pytest.mark.asyncio
    async def test_step7_post_provisions_and_redirects(self, client, session_cookie, app, tmp_path):
        from overblick.setup.wizard import _get_state, _derive_provisioner_state
        state = _get_state(app)
        state["principal"] = {"principal_name": "Alice", "principal_email": "",
                              "timezone": "Europe/Stockholm", "language_preference": "en"}
        state["llm"] = {
            "gateway_url": "http://127.0.0.1:8200",
            "local": {"enabled": True, "backend_type": "ollama",
                      "host": "127.0.0.1", "port": 11434, "model": "qwen3:8b"},
            "cloud": {"enabled": False, "backend_type": "ollama",
                      "host": "", "port": 11434, "model": "qwen3:8b"},
            "openai": {"enabled": False, "api_url": "https://api.openai.com/v1",
                       "model": "gpt-4o"},
            "default_backend": "local",
            "default_temperature": 0.7,
            "default_max_tokens": 2000,
        }
        state["communication"] = {"gmail_enabled": False, "gmail_address": "",
                                  "gmail_app_password": "", "telegram_enabled": False,
                                  "telegram_bot_token": "", "telegram_chat_id": ""}
        state["selected_use_cases"] = ["social_media"]
        state["assignments"] = {
            "social_media": {"personality": "cherry", "temperature": 0.8,
                             "max_tokens": 2000, "heartbeat_hours": 4, "quiet_hours": True},
        }
        _derive_provisioner_state(state)

        cookie_value, csrf_token = session_cookie

        # Patch provision in the module where it's used via local import
        with patch("overblick.setup.provisioner.provision") as mock_prov:
            mock_prov.return_value = {"created_files": ["config/overblick.yaml"]}
            resp = await client.post(
                "/settings/step/7",
                headers={"X-CSRF-Token": csrf_token},
                cookies={SESSION_COOKIE: cookie_value},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert "/settings/step/8" in resp.headers["location"]
        mock_prov.assert_called_once()
        # setup_needed should be cleared
        assert app.state.setup_needed is False

    @pytest.mark.asyncio
    async def test_step7_provisioning_failure_shows_error(self, client, session_cookie, app):
        from overblick.setup.wizard import _get_state, _derive_provisioner_state
        state = _get_state(app)
        state["selected_use_cases"] = ["social_media"]
        state["assignments"] = {
            "social_media": {"personality": "cherry", "temperature": 0.8,
                             "max_tokens": 2000, "heartbeat_hours": 4, "quiet_hours": True},
        }
        _derive_provisioner_state(state)

        cookie_value, csrf_token = session_cookie

        with patch("overblick.setup.provisioner.provision",
                   side_effect=RuntimeError("Disk full")):
            resp = await client.post(
                "/settings/step/7",
                headers={"X-CSRF-Token": csrf_token},
                cookies={SESSION_COOKIE: cookie_value},
                follow_redirects=False,
            )

        assert resp.status_code == 200
        assert "Disk full" in resp.text or "failed" in resp.text.lower()


class TestSettingsStep8:
    @pytest.mark.asyncio
    async def test_step8_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get(
            "/settings/step/8",
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "Return to Dashboard" in resp.text or "Dashboard" in resp.text


class TestSettingsTestEndpoints:
    @pytest.mark.asyncio
    async def test_test_ollama_endpoint_returns_html(self, client, session_cookie):
        """POST /settings/test/ollama returns HTML snippet (may fail if Ollama not running)."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/test/ollama",
            headers={"X-CSRF-Token": csrf_token},
            data={"host": "127.0.0.1", "port": "11434"},
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        # Should return either a success or error HTML badge
        assert "badge" in resp.text or "span" in resp.text

    @pytest.mark.asyncio
    async def test_api_models_endpoint(self, client, session_cookie):
        """POST /settings/api/models returns HTML (may fail if Ollama not running)."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/api/models",
            headers={"X-CSRF-Token": csrf_token},
            data={"host": "127.0.0.1", "port": "11434"},
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        # Should return select options or error badge
        assert "select" in resp.text or "badge" in resp.text or "Error" in resp.text

    @pytest.mark.asyncio
    async def test_test_gateway_endpoint(self, client, session_cookie):
        """POST /settings/test/gateway returns HTML snippet."""
        cookie_value, csrf_token = session_cookie
        resp = await client.post(
            "/settings/test/gateway",
            headers={"X-CSRF-Token": csrf_token},
            data={"gateway_url": "http://127.0.0.1:8200"},
            cookies={SESSION_COOKIE: cookie_value},
        )
        assert resp.status_code == 200
        assert "badge" in resp.text or "span" in resp.text
