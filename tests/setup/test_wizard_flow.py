"""
Tests for the wizard route handlers and flow.
"""

import pytest
from httpx import AsyncClient


class TestWelcomePage:
    """Step 1: Welcome page."""

    async def test_welcome_renders(self, client: AsyncClient):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "Överblick" in resp.text
        assert "Start Setup" in resp.text

    async def test_welcome_shows_version(self, client: AsyncClient):
        resp = await client.get("/")
        assert "0.1.0" in resp.text


class TestPrincipalStep:
    """Step 2: Principal identity."""

    async def test_step2_renders(self, client: AsyncClient):
        resp = await client.get("/step/2")
        assert resp.status_code == 200
        assert "Who are you?" in resp.text

    async def test_step2_valid_submission(self, client: AsyncClient):
        resp = await client.post(
            "/step/2",
            data={
                "principal_name": "Test User",
                "principal_email": "test@example.com",
                "timezone": "Europe/Stockholm",
                "language_preference": "en",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/step/3"

    async def test_step2_empty_name_shows_error(self, client: AsyncClient):
        resp = await client.post(
            "/step/2",
            data={
                "principal_name": "",
                "timezone": "Europe/Stockholm",
                "language_preference": "en",
            },
        )
        assert resp.status_code == 200
        # Should re-render the form (not redirect)
        assert "Who are you?" in resp.text


class TestLLMStep:
    """Step 3: LLM configuration."""

    async def test_step3_renders(self, client: AsyncClient):
        resp = await client.get("/step/3")
        assert resp.status_code == 200
        assert "AI Engine" in resp.text

    async def test_step3_valid_ollama(self, client: AsyncClient):
        resp = await client.post(
            "/step/3",
            data={
                "llm_provider": "ollama",
                "ollama_host": "127.0.0.1",
                "ollama_port": "11434",
                "model": "qwen3:8b",
                "gateway_url": "http://127.0.0.1:8200",
                "default_temperature": "0.7",
                "default_max_tokens": "2000",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/step/4"


class TestCommunicationStep:
    """Step 4: Communication channels."""

    async def test_step4_renders(self, client: AsyncClient):
        resp = await client.get("/step/4")
        assert resp.status_code == 200
        assert "Connect Your Channels" in resp.text

    async def test_step4_skip_everything(self, client: AsyncClient):
        resp = await client.post(
            "/step/4",
            data={},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/step/5"

    async def test_step4_with_gmail(self, client: AsyncClient):
        resp = await client.post(
            "/step/4",
            data={
                "gmail_enabled": "on",
                "gmail_address": "test@gmail.com",
                "gmail_app_password": "test-password",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303


class TestUseCaseStep:
    """Step 5: Use case selection."""

    async def test_step5_renders(self, client: AsyncClient):
        resp = await client.get("/step/5")
        assert resp.status_code == 200
        assert "What Should Your Agents Do?" in resp.text
        # Should list use cases
        assert "Social Media" in resp.text
        assert "Email Management" in resp.text

    async def test_step5_valid_selection(self, client: AsyncClient):
        resp = await client.post(
            "/step/5",
            data={"selected_use_cases": ["social_media", "email"]},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/step/6"

    async def test_step5_empty_selection_shows_error(self, client: AsyncClient):
        resp = await client.post(
            "/step/5",
            data={},
        )
        assert resp.status_code == 200
        assert "What Should Your Agents Do?" in resp.text

    async def test_step5_single_selection(self, client: AsyncClient):
        resp = await client.post(
            "/step/5",
            data={"selected_use_cases": ["research"]},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/step/6"


class TestAssignmentStep:
    """Step 6: Agent assignment."""

    async def test_step6_renders(self, client: AsyncClient):
        # First select some use cases
        await client.post(
            "/step/5",
            data={"selected_use_cases": ["social_media"]},
            follow_redirects=False,
        )
        resp = await client.get("/step/6")
        assert resp.status_code == 200
        assert "Who Handles What?" in resp.text

    async def test_step6_shows_personality_options(self, client: AsyncClient):
        """Social media should show multiple personality options."""
        await client.post(
            "/step/5",
            data={"selected_use_cases": ["social_media"]},
            follow_redirects=False,
        )
        resp = await client.get("/step/6")
        assert resp.status_code == 200
        # Should show personality grid for social media (multiple options)
        assert "personality-grid" in resp.text or "personality-option" in resp.text

    async def test_step6_auto_assigns_single_personality(self, client: AsyncClient):
        """Email should auto-assign Stal (only compatible personality)."""
        await client.post(
            "/step/5",
            data={"selected_use_cases": ["email"]},
            follow_redirects=False,
        )
        resp = await client.get("/step/6")
        assert resp.status_code == 200
        assert "Auto-assigned" in resp.text

    async def test_step6_post_redirects(self, client: AsyncClient):
        """Submitting step 6 should redirect to review."""
        await client.post(
            "/step/5",
            data={"selected_use_cases": ["social_media"]},
            follow_redirects=False,
        )
        resp = await client.post(
            "/step/6",
            data={
                "social_media_personality": "cherry",
                "social_media_temperature": "0.8",
                "social_media_max_tokens": "2000",
                "social_media_heartbeat_hours": "4",
                "social_media_quiet_hours": "on",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/step/7"


class TestReviewStep:
    """Step 7: Review."""

    async def test_step7_renders(self, client: AsyncClient):
        # Set up state through previous steps
        await client.post("/step/2", data={
            "principal_name": "Test User",
            "timezone": "Europe/Stockholm",
            "language_preference": "en",
        }, follow_redirects=False)
        await client.post("/step/3", data={
            "llm_provider": "ollama",
            "ollama_host": "127.0.0.1",
            "ollama_port": "11434",
            "model": "qwen3:8b",
            "gateway_url": "http://127.0.0.1:8200",
            "default_temperature": "0.7",
            "default_max_tokens": "2000",
        }, follow_redirects=False)
        await client.post("/step/4", data={}, follow_redirects=False)
        await client.post("/step/5", data={
            "selected_use_cases": ["social_media"],
        }, follow_redirects=False)
        await client.post("/step/6", data={
            "social_media_personality": "cherry",
            "social_media_temperature": "0.8",
            "social_media_max_tokens": "2000",
            "social_media_heartbeat_hours": "4",
        }, follow_redirects=False)

        resp = await client.get("/step/7")
        assert resp.status_code == 200
        assert "Review" in resp.text
        assert "Test User" in resp.text
        assert "Agent Assignments" in resp.text


class TestCompleteStep:
    """Step 8: Completion."""

    async def test_step8_renders(self, client: AsyncClient):
        resp = await client.get("/step/8")
        assert resp.status_code == 200
        assert "ready" in resp.text.lower() or "complete" in resp.text.lower()


class TestOllamaEndpoint:
    """Test endpoint for Ollama connectivity check."""

    async def test_ollama_unreachable(self, client: AsyncClient):
        resp = await client.post(
            "/test/ollama",
            data={"ollama_host": "127.0.0.1", "ollama_port": "99999"},
        )
        assert resp.status_code == 200
        assert "Not reachable" in resp.text


class TestBackNavigation:
    """Test that back navigation works between steps."""

    async def test_step_links(self, client: AsyncClient):
        """Each step should have a back link to the previous step."""
        resp = await client.get("/step/2")
        assert 'href="/"' in resp.text

        resp = await client.get("/step/3")
        assert 'href="/step/2"' in resp.text

        resp = await client.get("/step/4")
        assert 'href="/step/3"' in resp.text

        resp = await client.get("/step/5")
        assert 'href="/step/4"' in resp.text
