"""
Additional coverage tests for response_router module.

Covers uncovered lines:
- 160-162: LLM exception handling
- 177-179: raw client result (dict) handling
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.moltbook.response_router import ResponseRouter


class TestLLMException:
    @pytest.mark.asyncio
    async def test_should_return_safe_verdict_on_exception(self):
        """Lines 160-162: LLM exception returns safe (non-challenge) verdict."""
        llm = AsyncMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("Connection failed"))

        router = ResponseRouter(llm_pipeline=llm)
        data = {"verification": {"question": "test"}}

        verdict = await router.inspect(data)
        assert verdict is not None
        assert verdict.is_challenge is False
        assert "LLM error" in verdict.reason


class TestRawClientResult:
    @pytest.mark.asyncio
    async def test_should_handle_raw_dict_result(self):
        """Lines 177-179: raw client result (dict) path."""
        llm = AsyncMock()
        # Return a plain dict instead of an object with .blocked
        llm.chat = AsyncMock(return_value={"content": "NORMAL"})

        router = ResponseRouter(llm_pipeline=llm)
        data = {"verification": {"question": "test"}}

        verdict = await router.inspect(data)
        assert verdict is not None
        assert verdict.is_challenge is False

    @pytest.mark.asyncio
    async def test_should_handle_empty_raw_dict_result(self):
        """Lines 177-178: raw dict with no content returns safe verdict."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": ""})

        router = ResponseRouter(llm_pipeline=llm)
        data = {"verification": {"question": "test"}}

        verdict = await router.inspect(data)
        assert verdict is not None
        assert verdict.is_challenge is False
        assert "empty response" in verdict.reason

    @pytest.mark.asyncio
    async def test_should_handle_none_raw_result(self):
        """Line 177: None result returns safe verdict."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=None)

        router = ResponseRouter(llm_pipeline=llm)
        data = {"verification": {"question": "test"}}

        verdict = await router.inspect(data)
        assert verdict is not None
        assert verdict.is_challenge is False


class TestBlockedWithBlockStageNone:
    @pytest.mark.asyncio
    async def test_should_handle_blocked_with_no_block_stage(self):
        """Line 170: blocked result with block_stage=None."""
        llm = AsyncMock()
        llm.chat = AsyncMock(
            return_value=MagicMock(blocked=True, block_stage=None, block_reason="Safety")
        )

        router = ResponseRouter(llm_pipeline=llm)
        data = {"verification": {"question": "test"}}

        verdict = await router.inspect(data)
        assert verdict is not None
        assert verdict.is_challenge is False
        assert "Blocked" in verdict.reason
