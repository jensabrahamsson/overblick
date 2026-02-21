"""Tests for challenge handler."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from overblick.plugins.moltbook.challenge_handler import (
    PerContentChallengeHandler,
    deobfuscate_challenge,
    _strip_letter_doubling,
)


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

    def test_detect_deep_nested_comment_verification(self):
        """Issue #134: challenge data at data.comment.verification."""
        data = {
            "success": True,
            "comment": {
                "id": "abc",
                "verification": {
                    "challenge_text": "wHhAaTt iIsS 5 + 3?",
                    "verification_code": "nonce_xyz",
                    "respond_url": "/api/verify",
                },
            },
        }
        assert self.handler.detect(data, 200)

    def test_detect_deep_nested_post_verification(self):
        """Challenge nested under data.post.verification."""
        data = {
            "success": True,
            "post": {
                "id": "123",
                "verification": {
                    "question": "Solve this",
                    "nonce": "abc",
                },
            },
        }
        assert self.handler.detect(data, 200)

    def test_detect_deep_nested_data_verification(self):
        """Challenge nested under data.data.verification."""
        data = {
            "data": {
                "verification": {
                    "challenge_text": "What is 10?",
                    "token": "tok123",
                },
            },
        }
        assert self.handler.detect(data, 200)

    def test_detect_verification_code_as_nonce(self):
        """verification_code field recognized as nonce."""
        data = {"question": "What is 2+2?", "verification_code": "abc123"}
        assert self.handler.detect(data, 200)

    def test_silent_success_with_nested_challenge_detected(self):
        """API returns success: true but has nested challenge — must detect."""
        data = {
            "success": True,
            "comment": {
                "id": "c42",
                "content": "My comment",
                "verification": {
                    "challenge_text": "tWeNtY pLuS tHrEe",
                    "verification_code": "v_nonce_1",
                    "submit_url": "/api/v1/challenges/verify",
                    "time_limit": 300,
                },
            },
        }
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

    async def test_solve_uses_high_priority(self):
        """Challenge solving MUST use high priority to beat background tasks."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "42"})

        handler = PerContentChallengeHandler(llm_client=llm)
        await handler.solve({
            "question": "What is 6*7?",
            "nonce": "test",
        })

        call_kwargs = llm.chat.call_args.kwargs
        assert call_kwargs["priority"] == "high"

    async def test_solve_deobfuscates_question(self):
        """Challenge question is deobfuscated before sending to LLM."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "23"})

        handler = PerContentChallengeHandler(llm_client=llm)
        await handler.solve({
            "question": "wHhAaTt iIsS tWwEeNnTtYy pPlLuUsS tThHrReEeE?",
            "nonce": "test",
        })

        # LLM should receive deobfuscated text
        call_kwargs = llm.chat.call_args.kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        assert "twenty" in user_msg
        assert "three" in user_msg
        assert "tWwEeNnTtYy" not in user_msg

    async def test_extract_field_from_deep_nesting(self):
        """Fields are extracted from deeply nested paths like comment.verification."""
        handler = PerContentChallengeHandler(llm_client=AsyncMock())
        data = {
            "success": True,
            "comment": {
                "verification": {
                    "challenge_text": "What is 5+5?",
                    "verification_code": "nonce_abc",
                    "submit_url": "/verify",
                },
            },
        }
        question = handler._extract_field(data, handler.QUESTION_FIELDS)
        assert question == "What is 5+5?"
        nonce = handler._extract_field(data, handler.NONCE_FIELDS)
        assert nonce == "nonce_abc"
        endpoint = handler._extract_field(data, handler.ENDPOINT_FIELDS)
        assert endpoint == "/verify"


class TestDeobfuscation:
    """Tests for challenge text deobfuscation (issue #134 community findings)."""

    def test_strip_letter_doubling_basic(self):
        """tTwWeEnNtTyY → twenty (each char doubled with case swap)."""
        assert _strip_letter_doubling("tTwWeEnNtTyY") == "twenty"

    def test_strip_letter_doubling_mixed_case(self):
        """tWwEeNnTtYy → tWENTY → (caller lowercases)."""
        result = _strip_letter_doubling("tWwEeNnTtYy")
        assert result.lower() == "twenty"

    def test_strip_letter_doubling_short_word(self):
        """Short words (<4 chars) are left unchanged."""
        assert _strip_letter_doubling("iIsS") == "is"
        assert _strip_letter_doubling("a") == "a"
        assert _strip_letter_doubling("an") == "an"

    def test_strip_letter_doubling_no_doubles(self):
        """Words without doubling are left unchanged."""
        assert _strip_letter_doubling("hello") == "hello"
        assert _strip_letter_doubling("world") == "world"

    def test_deobfuscate_full_sentence(self):
        """Full challenge sentence with doubling + case mixing."""
        result = deobfuscate_challenge("wHhAaTt iIsS tWwEeNnTtYy pPlLuUsS tThHrReEeE?")
        assert "what" in result
        assert "twenty" in result
        assert "plus" in result
        assert "three" in result
        assert result.endswith("?")

    def test_deobfuscate_preserves_numbers(self):
        """Numeric tokens are preserved as-is."""
        result = deobfuscate_challenge("wHhAaTt iIsS 5 + 3?")
        assert "5" in result
        assert "3" in result
        assert "+" in result

    def test_deobfuscate_case_mixing_only(self):
        """Case mixing without doubling: tWeNtY → twenty."""
        result = deobfuscate_challenge("tWeNtY pLuS tHrEe")
        assert "twenty" in result
        assert "plus" in result
        assert "three" in result

    def test_deobfuscate_preserves_operators(self):
        """Math operators and punctuation are preserved."""
        result = deobfuscate_challenge("5 + 3 = ?")
        assert result == "5 + 3 = ?"

    def test_deobfuscate_empty_string(self):
        assert deobfuscate_challenge("") == ""

    def test_deobfuscate_realistic_challenge(self):
        """Realistic challenge from BabyDino327's report."""
        result = deobfuscate_challenge("tWwEeNnTtYy tThHrReEeE")
        # Should get "twenty three" (the deobfuscated word-form numbers)
        assert "twenty" in result
        assert "three" in result
