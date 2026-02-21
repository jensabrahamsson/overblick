"""Tests for LLM-based response router (challenge detection)."""

import pytest
from unittest.mock import AsyncMock

from overblick.plugins.moltbook.response_router import (
    ResponseRouter,
    RouterVerdict,
)


class TestPreFilter:
    """Tests for the cheap pre-filter that skips obvious non-challenges."""

    def setup_method(self):
        self.router = ResponseRouter(llm_client=AsyncMock())

    def test_normal_post_response_not_suspicious(self):
        """Standard post creation response should be filtered out."""
        data = {
            "success": True,
            "post": {"id": "p123", "title": "My Post", "content": "Hello"},
        }
        assert not self.router._is_suspicious(data)

    def test_normal_comment_response_not_suspicious(self):
        """Standard comment response should be filtered out."""
        data = {
            "success": True,
            "comment": {"id": "c456", "content": "Nice post!"},
        }
        assert not self.router._is_suspicious(data)

    def test_verification_key_is_suspicious(self):
        """Response with 'verification' key nested inside should be suspicious."""
        data = {
            "success": True,
            "comment": {
                "id": "c789",
                "verification": {"challenge_text": "solve this"},
            },
        }
        assert self.router._is_suspicious(data)

    def test_challenge_key_is_suspicious(self):
        data = {"challenge": {"question": "What is 2+2?"}}
        assert self.router._is_suspicious(data)

    def test_nonce_key_is_suspicious(self):
        data = {"success": True, "nonce": "abc123"}
        assert self.router._is_suspicious(data)

    def test_deeply_nested_verification_detected(self):
        """Pre-filter searches up to 4 levels deep."""
        data = {
            "data": {
                "response": {
                    "meta": {
                        "verification": {"text": "solve"},
                    },
                },
            },
        }
        assert self.router._is_suspicious(data)

    def test_max_depth_respected(self):
        """Keys beyond max_depth are not checked."""
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "level4": {
                            "level5": {"verification": True},
                        },
                    },
                },
            },
        }
        # At depth 4, level5 is not reached
        assert not self.router._is_suspicious(data)


@pytest.mark.asyncio
class TestLLMClassification:
    async def test_challenge_detected(self):
        """LLM says CHALLENGE → verdict.is_challenge = True."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "CHALLENGE"})

        router = ResponseRouter(llm_client=llm)
        data = {
            "success": True,
            "comment": {
                "verification": {"challenge_text": "What is 5+3?", "nonce": "abc"},
            },
        }

        verdict = await router.inspect(data)
        assert verdict is not None
        assert verdict.is_challenge is True
        assert router._stats["challenges_found"] == 1

    async def test_normal_response_after_prefilter(self):
        """Normal response → pre-filtered → None returned, no LLM call."""
        llm = AsyncMock()
        router = ResponseRouter(llm_client=llm)

        verdict = await router.inspect({"success": True, "post": {"id": "p1"}})
        assert verdict is None
        llm.chat.assert_not_called()
        assert router._stats["inspections_prefiltered"] == 1

    async def test_normal_verdict_from_llm(self):
        """LLM says NORMAL → verdict.is_challenge = False."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "NORMAL"})

        router = ResponseRouter(llm_client=llm)
        # Has 'verification' key to pass pre-filter, but LLM says normal
        data = {"verification": "email_verified", "user": "test"}

        verdict = await router.inspect(data)
        assert verdict is not None
        assert verdict.is_challenge is False

    async def test_llm_error_returns_safe_verdict(self):
        """LLM failure → verdict.is_challenge = False (safe fallback)."""
        llm = AsyncMock()
        llm.chat = AsyncMock(side_effect=Exception("LLM down"))

        router = ResponseRouter(llm_client=llm)
        data = {"verification": {"question": "test"}}

        verdict = await router.inspect(data)
        assert verdict is not None
        assert verdict.is_challenge is False

    async def test_uses_high_priority(self):
        """Challenge classification must use high priority in LLM gateway."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "NORMAL"})

        router = ResponseRouter(llm_client=llm)
        data = {"verification": {"nonce": "abc"}}

        await router.inspect(data)
        call_kwargs = llm.chat.call_args.kwargs
        assert call_kwargs["priority"] == "high"

    async def test_truncates_large_responses(self):
        """Large responses are truncated before sending to LLM."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "NORMAL"})

        router = ResponseRouter(llm_client=llm, max_response_size=100)
        data = {"verification": {"data": "x" * 500}}

        await router.inspect(data)
        call_kwargs = llm.chat.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        assert "truncated" in user_msg

    async def test_stats_tracking(self):
        """Stats are tracked correctly across multiple inspections."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "CHALLENGE"})

        router = ResponseRouter(llm_client=llm)

        # Normal response (filtered)
        await router.inspect({"post": {"id": "1"}})
        # Suspicious response (LLM called)
        await router.inspect({"verification": {"q": "test"}})

        stats = router.get_stats()
        assert stats["inspections_total"] == 2
        assert stats["inspections_prefiltered"] == 1
        assert stats["inspections_llm"] == 1
        assert stats["challenges_found"] == 1
