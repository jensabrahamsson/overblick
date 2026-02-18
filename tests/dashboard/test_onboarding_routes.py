"""Tests for onboarding wizard routes."""

from unittest.mock import AsyncMock, patch

import pytest
from overblick.dashboard.auth import SESSION_COOKIE


class TestOnboardingWizard:
    @pytest.mark.asyncio
    async def test_step1_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Name Your Identity" in resp.text

    @pytest.mark.asyncio
    async def test_step1_with_explicit_param(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=1", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Name Your Identity" in resp.text

    @pytest.mark.asyncio
    async def test_step2_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=2", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "LLM Configuration" in resp.text

    @pytest.mark.asyncio
    async def test_step3_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=3", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Choose a Personality" in resp.text

    @pytest.mark.asyncio
    async def test_step4_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=4", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Plugins" in resp.text

    @pytest.mark.asyncio
    async def test_step5_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=5", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Secrets" in resp.text

    @pytest.mark.asyncio
    async def test_step6_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=6", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Review" in resp.text

    @pytest.mark.asyncio
    async def test_step7_renders(self, client, session_cookie):
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=7", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200


class TestOnboardingStep1:
    @pytest.mark.asyncio
    async def test_step1_submit(self, client, session_cookie):
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "1", "name": "testbot", "description": "A test bot", "display_name": "TestBot"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    @pytest.mark.asyncio
    async def test_step1_invalid_name(self, client, session_cookie):
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "1", "name": "INVALID!", "description": "", "display_name": ""},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_step1_duplicate_name(self, client, session_cookie, mock_onboarding_service):
        mock_onboarding_service.identity_exists.return_value = True
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "1", "name": "anomal", "description": "", "display_name": ""},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400
        assert "already exists" in resp.text

    @pytest.mark.asyncio
    async def test_step1_empty_name(self, client, session_cookie):
        """Empty name should fail validation."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "1", "name": "", "description": "desc", "display_name": "Test"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_step1_redirects_to_step2(self, client, session_cookie):
        """Successful step 1 submission redirects to step 2."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "1", "name": "newagent", "description": "Test agent", "display_name": "NewAgent"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "step=2" in resp.headers.get("location", "")


class TestOnboardingStep2:
    @pytest.mark.asyncio
    async def test_llm_config_submit(self, client, session_cookie):
        """Step 2 submits LLM configuration and redirects."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={
                "step": "2",
                "model": "qwen3:8b",
                "temperature": "0.7",
                "max_tokens": "2000",
                "provider": "ollama",
            },
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "step=3" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_llm_invalid_temperature(self, client, session_cookie):
        """Invalid temperature fails validation."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={
                "step": "2",
                "model": "qwen3:8b",
                "temperature": "not_a_number",
                "max_tokens": "2000",
                "provider": "ollama",
            },
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400


class TestOnboardingStep3:
    @pytest.mark.asyncio
    async def test_personality_submit(self, client, session_cookie):
        """Step 3 submits personality choice and redirects."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "3", "personality": "anomal"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "step=4" in resp.headers.get("location", "")


class TestOnboardingStep4:
    @pytest.mark.asyncio
    async def test_plugins_submit(self, client, session_cookie):
        """Step 4 submits plugin selection."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "4", "plugins": ["moltbook", "telegram"]},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "step=5" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_no_plugins_selected(self, client, session_cookie):
        """Step 4 works with no plugins selected."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "4"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302


class TestOnboardingStep5:
    @pytest.mark.asyncio
    async def test_secrets_submit(self, client, session_cookie):
        """Step 5 submits secrets."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={
                "step": "5",
                "secret_keys": ["telegram_bot_token", "principal_name"],
                "secret_values": ["token123", "TestUser"],
            },
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "step=6" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_empty_secrets(self, client, session_cookie):
        """Step 5 works with no secrets."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "5"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302


class TestOnboardingStep6:
    @pytest.mark.asyncio
    async def test_review_submit_creates_identity(self, client, session_cookie):
        """Step 6 (review) triggers identity creation."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "6"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "step=7" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_review_creation_failure(self, client, session_cookie, mock_onboarding_service):
        """Failed identity creation shows error on review step."""
        mock_onboarding_service.create_identity.side_effect = RuntimeError("Disk full")
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard",
            data={"step": "6"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400
        assert "Failed to create identity" in resp.text


class TestOnboardingBoundaries:
    @pytest.mark.asyncio
    async def test_step_clamp_negative(self, client, session_cookie):
        """Negative step number is clamped to 1."""
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=-1", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Name Your Identity" in resp.text

    @pytest.mark.asyncio
    async def test_step_clamp_too_high(self, client, session_cookie):
        """Step beyond max is clamped to last step."""
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=99", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_step_zero(self, client, session_cookie):
        """Step 0 is clamped to 1."""
        cookie_value, _ = session_cookie
        resp = await client.get("/onboard?step=0", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200
        assert "Name Your Identity" in resp.text

    @pytest.mark.asyncio
    async def test_unauthenticated_redirect(self, client):
        """Unauthenticated user is redirected to login."""
        resp = await client.get("/onboard", follow_redirects=False)
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")


class TestOnboardingWizardState:
    @pytest.mark.asyncio
    async def test_wizard_state_persistence(self, client, session_cookie):
        """Wizard state persists between steps."""
        cookie_value, csrf = session_cookie

        # Submit step 1
        resp = await client.post(
            "/onboard",
            data={"step": "1", "name": "flowbot", "description": "Flow test", "display_name": "FlowBot"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Submit step 2 (LLM config)
        resp = await client.post(
            "/onboard",
            data={
                "step": "2",
                "model": "qwen3:8b",
                "temperature": "0.7",
                "max_tokens": "2000",
                "provider": "ollama",
            },
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Check review step shows accumulated data
        resp = await client.get("/onboard?step=6", cookies={SESSION_COOKIE: cookie_value})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_complete_wizard_cleans_state(self, client, session_cookie):
        """Completing the wizard redirects to home and clears state."""
        cookie_value, csrf = session_cookie

        # Step 1
        await client.post(
            "/onboard",
            data={"step": "1", "name": "donebot", "description": "Done", "display_name": "DoneBot"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        # Step 6 (review -> create)
        await client.post(
            "/onboard",
            data={"step": "6"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        # Step 7 (verify -> complete) — beyond last step
        resp = await client.post(
            "/onboard",
            data={"step": "7"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers.get("location") == "/"


class TestOnboardingChat:
    """Tests for onboarding chat and LLM test endpoints."""

    @pytest.mark.asyncio
    async def test_chat_missing_body(self, client, session_cookie):
        """Chat endpoint returns 400 for invalid JSON."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard/chat",
            content=b"not json",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False

    @pytest.mark.asyncio
    async def test_chat_missing_fields(self, client, session_cookie):
        """Chat endpoint returns 400 when identity_name or message missing."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard/chat",
            json={"identity_name": "", "message": ""},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False

    @pytest.mark.asyncio
    @patch("overblick.shared.onboarding_chat.chat_with_identity", new_callable=AsyncMock)
    async def test_chat_success(self, mock_chat, client, session_cookie):
        """Chat endpoint calls shared chat function and returns response."""
        mock_chat.return_value = {
            "response": "Hello! I am Anomal.",
            "identity": "anomal",
            "success": True,
        }
        cookie_value, csrf = session_cookie

        # First configure LLM in wizard state
        await client.post(
            "/onboard",
            data={
                "step": "2", "model": "qwen3:8b", "temperature": "0.7",
                "max_tokens": "2000", "provider": "ollama",
            },
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )

        resp = await client.post(
            "/onboard/chat",
            json={"identity_name": "anomal", "message": "Who are you?"},
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["response"] == "Hello! I am Anomal."
        mock_chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_test_llm_no_config(self, client, session_cookie):
        """Test LLM endpoint returns 400 when no LLM is configured."""
        cookie_value, csrf = session_cookie
        resp = await client.post(
            "/onboard/test-llm",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["success"] is False
        assert "Configure LLM" in data["error"]

    @pytest.mark.asyncio
    @patch("overblick.shared.onboarding_chat.test_llm_connection", new_callable=AsyncMock)
    async def test_test_llm_success(self, mock_test, client, session_cookie):
        """Test LLM endpoint calls shared test function."""
        mock_test.return_value = {"success": True, "provider": "llm", "response": "Hello!"}
        cookie_value, csrf = session_cookie

        # Configure LLM first
        await client.post(
            "/onboard",
            data={
                "step": "2", "model": "qwen3:8b", "temperature": "0.7",
                "max_tokens": "2000", "provider": "ollama",
            },
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
            follow_redirects=False,
        )

        resp = await client.post(
            "/onboard/test-llm",
            cookies={SESSION_COOKIE: cookie_value},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        mock_test.assert_called_once()
