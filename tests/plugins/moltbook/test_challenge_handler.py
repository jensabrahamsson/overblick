"""Tests for challenge handler."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from blick.plugins.moltbook.challenge_handler import PerContentChallengeHandler


class TestChallengeDetection:
    def setup_method(self):
        self.handler = PerContentChallengeHandler(
            llm_client=AsyncMock(),
        )

    def test_detect_nonce_and_question(self):
        data = {"nonce": "abc123", "question": "What is 2+2?"}
        assert self.handler.detect(data, 200)

    def test_detect_typed_challenge(self):
        data = {"type": "challenge", "data": "something"}
        assert self.handler.detect(data, 200)

    def test_detect_typed_verification(self):
        data = {"type": "verification", "prompt": "solve this"}
        assert self.handler.detect(data, 200)

    def test_detect_question_and_endpoint(self):
        data = {"question": "What is 3*5?", "respond_url": "/api/v1/verify"}
        assert self.handler.detect(data, 200)

    def test_detect_nested_challenge(self):
        data = {"challenge": {"question": "Solve", "nonce": "xyz"}}
        assert self.handler.detect(data, 200)

    def test_normal_response_not_detected(self):
        data = {"success": True, "post": {"id": "123", "title": "Test"}}
        assert not self.handler.detect(data, 200)

    def test_empty_dict_not_detected(self):
        assert not self.handler.detect({}, 200)

    def test_non_dict_not_detected(self):
        assert not self.handler.detect("string", 200)

    def test_nonce_and_endpoint(self):
        data = {"nonce": "abc", "submit_url": "/verify"}
        assert self.handler.detect(data, 200)


@pytest.mark.asyncio
class TestChallengeSolving:
    async def test_solve_success(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "4"})

        session = MagicMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value='{"success": true}')
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)
        session.post = MagicMock(return_value=mock_response)

        handler = PerContentChallengeHandler(
            llm_client=llm, http_session=session, base_url="https://api.test.com",
        )

        result = await handler.solve({
            "question": "What is 2+2?",
            "nonce": "abc",
            "respond_url": "/verify",
        })

        assert result is not None
        assert handler._stats["challenges_solved"] == 1

    async def test_solve_no_question(self):
        handler = PerContentChallengeHandler(llm_client=AsyncMock())
        result = await handler.solve({"nonce": "abc"})
        assert result is None
        assert handler._stats["challenges_failed"] == 1

    async def test_solve_llm_error(self):
        llm = AsyncMock()
        llm.chat = AsyncMock(side_effect=Exception("LLM down"))

        handler = PerContentChallengeHandler(llm_client=llm)
        result = await handler.solve({"question": "test?", "nonce": "x"})
        assert result is None

    async def test_get_stats(self):
        handler = PerContentChallengeHandler(llm_client=AsyncMock())
        stats = handler.get_stats()
        assert "challenges_detected" in stats
        assert stats["challenges_solved"] == 0
