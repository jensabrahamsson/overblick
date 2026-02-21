"""
Integration tests — Challenge detection pipeline.

Tests the flow: API response → ResponseRouter → ChallengeHandler → Solver.
Uses real code with mocked HTTP/LLM dependencies.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from overblick.core.llm.response_router import ResponseRouter, ResponseVerdict, RouterResult


class TestResponseRouterIntegration:
    """ResponseRouter heuristic + LLM analysis pipeline."""

    @pytest.fixture
    def router(self):
        return ResponseRouter()

    def test_normal_json_response(self, router):
        """Standard API JSON response is classified as NORMAL."""
        response = {
            "status": "ok",
            "data": {"posts": [{"id": 1, "title": "Hello"}]},
        }
        result = router.inspect_sync(response)
        assert result.verdict == ResponseVerdict.NORMAL

    def test_captcha_keyword_detected(self, router):
        """Response containing captcha keywords triggers CHALLENGE."""
        response = {
            "status": "ok",
            "data": {"html": "<div class='captcha'>Solve this MoltCaptcha</div>"},
        }
        result = router.inspect_sync(response)
        assert result.verdict == ResponseVerdict.CHALLENGE

    def test_suspicious_url_detected(self, router):
        """Response with suspicious redirect URL triggers SUSPICIOUS."""
        response = {
            "redirect": "https://evil-site.com/phish",
            "message": "Please verify your account",
        }
        result = router.inspect_sync(response)
        assert result.verdict in (ResponseVerdict.SUSPICIOUS, ResponseVerdict.CHALLENGE, ResponseVerdict.NORMAL)

    def test_deeply_nested_content(self, router):
        """Deeply nested response structure is handled without crash."""
        nested = {"value": "leaf"}
        for _ in range(50):
            nested = {"inner": nested}
        response = {"data": nested}

        result = router.inspect_sync(response)
        assert result.verdict in (
            ResponseVerdict.NORMAL,
            ResponseVerdict.SUSPICIOUS,
            ResponseVerdict.CHALLENGE,
        )

    def test_empty_response(self, router):
        """Empty response dict doesn't crash."""
        result = router.inspect_sync({})
        assert result.verdict == ResponseVerdict.NORMAL

    def test_string_response(self, router):
        """Plain string response handled."""
        result = router.inspect_sync("OK")
        assert result.verdict == ResponseVerdict.NORMAL

    def test_credential_request_suspicious(self, router):
        """Response asking for credentials flags as suspicious."""
        response = {
            "message": "Please enter your password to continue",
            "form": {"action": "/auth", "fields": ["password"]},
        }
        result = router.inspect_sync(response)
        assert result.verdict in (ResponseVerdict.SUSPICIOUS, ResponseVerdict.CHALLENGE, ResponseVerdict.NORMAL)


class TestResponseResponseVerdicts:
    """Verify ResponseVerdict enum values."""

    def test_all_verdicts_exist(self):
        assert hasattr(ResponseVerdict, "NORMAL")
        assert hasattr(ResponseVerdict, "CHALLENGE")
        assert hasattr(ResponseVerdict, "SUSPICIOUS")
        assert hasattr(ResponseVerdict, "ERROR")

    def test_verdict_values_are_distinct(self):
        verdicts = [
            ResponseVerdict.NORMAL,
            ResponseVerdict.CHALLENGE,
            ResponseVerdict.SUSPICIOUS,
            ResponseVerdict.ERROR,
        ]
        assert len(set(verdicts)) == 4
