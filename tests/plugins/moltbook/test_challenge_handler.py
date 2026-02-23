"""Tests for challenge handler."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from overblick.plugins.moltbook.challenge_handler import (
    PerContentChallengeHandler,
    deobfuscate_challenge,
    solve_arithmetic,
    _strip_letter_doubling,
    _extract_word_numbers,
    _fuzzy_match,
)


def _make_mock_session(status=200, body='{"success": true}'):
    """Create a mock aiohttp session that returns a predictable response."""
    session = MagicMock()
    mock_response = AsyncMock()
    mock_response.status = status
    mock_response.text = AsyncMock(return_value=body)
    mock_response.json = AsyncMock(return_value={"success": True})
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    session.post = MagicMock(return_value=mock_response)
    session.patch = MagicMock(return_value=mock_response)
    return session


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

    def test_detect_official_api_format(self):
        """Official API spec format with verification_required + code field."""
        data = {
            "verification_required": True,
            "verification": {
                "code": "moltbook_verify_abc123",
                "challenge": "If you have 15 apples and give away 7, how many remain?",
                "instructions": "Respond with ONLY the number",
            },
        }
        assert self.handler.detect(data, 200)

    def test_detect_verification_required_flag(self):
        """Top-level verification_required=True is enough to detect."""
        data = {"verification_required": True, "other": "stuff"}
        assert self.handler.detect(data, 200)

    def test_detect_code_as_nonce(self):
        """Plain 'code' field recognized as nonce."""
        data = {"challenge": "What is 2+2?", "code": "moltbook_verify_xyz"}
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
    async def test_solve_success_via_verify_endpoint(self):
        """Solve succeeds via POST /verify (strategy 1)."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "4"})
        session = _make_mock_session()

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://api.test.com",
        )

        result = await handler.solve(
            {
                "question": "What is 2+2?",
                "nonce": "abc",
            }
        )

        assert result is not None
        assert handler._stats["challenges_solved"] == 1
        # Verify POST was made to /verify endpoint
        session.post.assert_called()
        call_url = session.post.call_args[0][0]
        assert "/verify" in call_url

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
        """LLM calls MUST use high priority."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "spider"})

        handler = PerContentChallengeHandler(llm_client=llm)
        await handler.solve(
            {
                "question": "What animal has 8 legs?",
                "nonce": "test",
            }
        )

        call_kwargs = llm.chat.call_args_list[0].kwargs
        assert call_kwargs["priority"] == "high"

    async def test_solve_deobfuscates_question(self):
        """Challenge question is deobfuscated before sending to LLM."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "lobster"})

        handler = PerContentChallengeHandler(llm_client=llm)
        await handler.solve(
            {
                "question": "wHhAaTt aNnIiMmAaLl hHaAsS cClLaAwWsS?",
                "nonce": "test",
            }
        )

        # LLM should receive deobfuscated text
        call_kwargs = llm.chat.call_args_list[0].kwargs
        user_msg = call_kwargs["messages"][1]["content"]
        assert "animal" in user_msg
        assert "claws" in user_msg
        assert "wHhAaTt" not in user_msg

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

    async def test_extract_field_official_api_format(self):
        """Extract fields from the official API challenge format."""
        handler = PerContentChallengeHandler(llm_client=AsyncMock())
        data = {
            "verification_required": True,
            "verification": {
                "code": "moltbook_verify_abc",
                "challenge": "How many remain from 15 - 7?",
                "instructions": "Respond with ONLY the number",
            },
        }
        question = handler._extract_field(data, handler.QUESTION_FIELDS)
        assert question == "How many remain from 15 - 7?"
        nonce = handler._extract_field(data, handler.NONCE_FIELDS)
        assert nonce == "moltbook_verify_abc"


@pytest.mark.asyncio
class TestComplexityRouting:
    """Tests for complexity-based LLM routing via gateway."""

    async def test_ultra_complexity_tried_first(self):
        """Ultra complexity is tried before local LLM."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "50"})
        session = _make_mock_session()

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://api.test.com",
        )

        # Use a non-arithmetic question so arithmetic fast-path doesn't trigger
        result = await handler.solve(
            {
                "question": "What color is the sky?",
                "verification_code": "nonce_1",
            }
        )

        assert result is not None
        # First call should be complexity="ultra"
        first_call = llm.chat.call_args_list[0]
        assert first_call.kwargs["complexity"] == "ultra"

    async def test_fallback_to_local_on_ultra_failure(self):
        """When ultra LLM fails, local LLM (complexity=low) is used as fallback."""
        llm = AsyncMock()
        # First call (ultra) fails, second call (low) succeeds
        llm.chat = AsyncMock(
            side_effect=[
                Exception("Ultra backend unavailable"),
                {"content": "blue"},
            ]
        )
        session = _make_mock_session()

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://api.test.com",
        )

        result = await handler.solve(
            {
                "question": "What color is the sky?",
                "verification_code": "nonce_1",
            }
        )

        assert result is not None
        assert llm.chat.call_count == 2
        # First call: ultra
        assert llm.chat.call_args_list[0].kwargs["complexity"] == "ultra"
        # Second call: low
        assert llm.chat.call_args_list[1].kwargs["complexity"] == "low"

    async def test_arithmetic_fallback_when_llm_fails(self):
        """Arithmetic solver is used as fallback when all LLMs fail."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=None)  # Both LLM attempts fail
        session = _make_mock_session()

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://api.test.com",
        )

        result = await handler.solve(
            {
                "question": "What is 32 + 18?",
                "verification_code": "nonce_1",
            }
        )

        assert result is not None
        # LLM should be called first (ultra then low), both failed
        assert llm.chat.call_count == 2
        # Arithmetic fallback handles it — verify answer was submitted
        assert handler.get_stats()["challenges_solved"] == 1

    async def test_llm_params_deterministic(self):
        """LLM calls use deterministic parameters: temperature=0.0, max_tokens=512, priority=high."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "answer"})
        session = _make_mock_session()

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://api.test.com",
        )

        await handler.solve(
            {
                "question": "What color is the sky?",
                "verification_code": "nonce_1",
            }
        )

        call_kwargs = llm.chat.call_args_list[0].kwargs
        assert call_kwargs["temperature"] == 0.0
        assert call_kwargs["max_tokens"] == 512
        assert call_kwargs["priority"] == "high"


@pytest.mark.asyncio
class TestSubmitStrategy:
    """Tests for the multi-strategy challenge submission."""

    async def test_verify_endpoint_is_primary(self):
        """POST /verify is tried first, even when challenge data has no endpoint."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "50"})
        session = _make_mock_session()

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://www.moltbook.com/api/v1",
        )

        result = await handler.solve(
            {
                "question": "What is 25+25?",
                "verification_code": "nonce_1",
            }
        )

        assert result is not None
        # POST was made to /verify
        call_url = session.post.call_args[0][0]
        assert call_url == "https://www.moltbook.com/api/v1/verify"

    async def test_retry_original_endpoint(self):
        """When /verify fails, retry original endpoint with verification fields."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "50"})

        # First call (to /verify) fails, second (retry) succeeds
        fail_response = AsyncMock()
        fail_response.status = 404
        fail_response.text = AsyncMock(return_value='{"error": "not found"}')
        fail_response.headers = {}
        fail_response.__aenter__ = AsyncMock(return_value=fail_response)
        fail_response.__aexit__ = AsyncMock(return_value=False)

        success_response = AsyncMock()
        success_response.status = 200
        success_response.text = AsyncMock(return_value='{"success": true}')
        success_response.headers = {"Content-Type": "application/json"}
        success_response.__aenter__ = AsyncMock(return_value=success_response)
        success_response.__aexit__ = AsyncMock(return_value=False)

        session = MagicMock()
        session.post = MagicMock(side_effect=[fail_response, success_response])

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://www.moltbook.com/api/v1",
        )

        result = await handler.solve(
            {"question": "What is 25+25?", "verification_code": "nonce_1"},
            original_endpoint="/posts/123/comments",
            original_payload={"content": "Hello"},
        )

        assert result is not None
        # Two POSTs: first /verify (failed), then retry original
        assert session.post.call_count == 2

    async def test_answer_formatted_to_2_decimals(self):
        """Numeric answers are formatted to 2 decimal places per API convention."""
        handler = PerContentChallengeHandler(llm_client=AsyncMock())
        assert handler._format_answer("50") == "50.00"
        assert handler._format_answer("3.5") == "3.50"
        assert handler._format_answer("100") == "100.00"
        assert handler._format_answer("lobster") == "lobster"
        assert handler._format_answer("[1, 2, 3]") == "[1, 2, 3]"

    async def test_verify_payload_includes_verification_code(self):
        """POST /verify payload includes verification_code field."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "50"})
        session = _make_mock_session()

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://api.test.com",
        )

        await handler.solve(
            {
                "question": "What is 25+25?",
                "verification_code": "nonce_abc",
            }
        )

        call_kwargs = session.post.call_args[1]
        payload = call_kwargs["json"]
        assert payload["answer"] == "50.00"
        assert payload["verification_code"] == "nonce_abc"


@pytest.mark.asyncio
class TestAuditEvents:
    """Tests for audit logging in challenge handler."""

    async def test_challenge_received_audit(self):
        """challenge_received event is logged when solve() starts."""
        audit_log = MagicMock()
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "50"})
        session = _make_mock_session()

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://api.test.com",
            audit_log=audit_log,
        )

        await handler.solve(
            {
                "question": "What is 25+25?",
                "verification_code": "nonce_1",
            }
        )

        # Find challenge_received call
        received_calls = [
            c
            for c in audit_log.log.call_args_list
            if c.kwargs.get("action") == "challenge_received"
        ]
        assert len(received_calls) == 1
        details = received_calls[0].kwargs["details"]
        assert details["nonce"] == "nonce_1"
        assert "25+25" in details["question_raw"]

    async def test_challenge_response_audit(self):
        """challenge_response event is logged after solving."""
        audit_log = MagicMock()
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "50"})
        session = _make_mock_session()

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://api.test.com",
            audit_log=audit_log,
        )

        await handler.solve(
            {
                "question": "What is 25+25?",
                "verification_code": "nonce_1",
            }
        )

        response_calls = [
            c
            for c in audit_log.log.call_args_list
            if c.kwargs.get("action") == "challenge_response"
        ]
        assert len(response_calls) == 1
        details = response_calls[0].kwargs["details"]
        assert details["answer"] == "50"
        assert details["solver"] == "cloud_llm"
        assert details["duration_ms"] > 0

    async def test_challenge_submitted_audit(self):
        """challenge_submitted event is logged after submission."""
        audit_log = MagicMock()
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "50"})
        session = _make_mock_session()

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://api.test.com",
            audit_log=audit_log,
        )

        await handler.solve(
            {
                "question": "What is 25+25?",
                "verification_code": "nonce_1",
            }
        )

        submitted_calls = [
            c
            for c in audit_log.log.call_args_list
            if c.kwargs.get("action") == "challenge_submitted"
        ]
        assert len(submitted_calls) == 1
        details = submitted_calls[0].kwargs["details"]
        assert details["method"] == "post_verify"
        assert details["success"] is True


@pytest.mark.asyncio
class TestDBRecording:
    """Tests for challenge history recording in engagement DB."""

    async def test_record_challenge_called(self):
        """record_challenge() is called after challenge attempt."""
        engagement_db = AsyncMock()
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "50"})
        session = _make_mock_session()

        handler = PerContentChallengeHandler(
            llm_client=llm,
            http_session=session,
            base_url="https://api.test.com",
            engagement_db=engagement_db,
        )

        await handler.solve(
            {
                "question": "What is 25+25?",
                "verification_code": "nonce_1",
            }
        )

        engagement_db.record_challenge.assert_called_once()
        call_kwargs = engagement_db.record_challenge.call_args.kwargs
        assert call_kwargs["challenge_id"] == "nonce_1"
        assert call_kwargs["answer"] == "50"
        assert call_kwargs["solver"] == "cloud_llm"
        assert call_kwargs["correct"] is True
        assert call_kwargs["duration_ms"] > 0

    async def test_record_challenge_on_failure(self):
        """record_challenge() records failed attempts too."""
        engagement_db = AsyncMock()
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "50"})

        # No session = no submit possible
        handler = PerContentChallengeHandler(
            llm_client=llm,
            engagement_db=engagement_db,
        )

        await handler.solve(
            {
                "question": "What is 25+25?",
                "verification_code": "nonce_1",
            }
        )

        engagement_db.record_challenge.assert_called_once()
        call_kwargs = engagement_db.record_challenge.call_args.kwargs
        assert call_kwargs["correct"] is False
        assert call_kwargs["error"] == "submit_failed"


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
        """Words without obfuscation doubling are left unchanged.

        Natural same-case doubles (ll in hello) are preserved because
        the pair scanner only strips opposite-case pairs (Ll, not ll).
        """
        assert _strip_letter_doubling("hello") == "hello"
        assert _strip_letter_doubling("world") == "world"
        assert _strip_letter_doubling("llama") == "llama"

    def test_strip_letter_doubling_partial(self):
        """Partial doubling is handled (not all chars doubled).

        lOoBbSsStTeR: Oo, Bb, Ss are opposite-case pairs (stripped),
        remaining SS collapsed in pass 2.
        """
        result = _strip_letter_doubling("lOoBbSsStTeR")
        assert result.lower() == "lobster"

    def test_deobfuscate_with_punctuation_noise(self):
        """Obfuscation punctuation injected between letters is stripped.

        This is the bug that caused 10 failures: lO.oBbSsStTeR
        Alpha extraction: lOoBbSsStTeR → pair strip + collapse → lobster.
        """
        result = deobfuscate_challenge("lO.oBbSsStTeR")
        assert result == "lobster"

    def test_deobfuscate_various_punctuation(self):
        """Various obfuscation characters (^ / ~ + < >) are stripped.

        h^Ee~Ll/Ll+Oo → alpha: hEeLlLlOo
        Pairs: Ee stripped, Ll stripped + trailing L consumed, l kept, Oo stripped
        Result: hELlO → lower: hello
        """
        assert deobfuscate_challenge("h^Ee~Ll/Ll+Oo") == "hello"

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
        """Case mixing without doubling: tWeNtY → twenty.

        Note: tHrEe → thre (Ee is opposite-case, indistinguishable from
        obfuscation double). Acceptable for LLM solving.
        """
        result = deobfuscate_challenge("tWeNtY pLuS tHrEe")
        assert "twenty" in result
        assert "plus" in result
        # tHrEe → thre (ambiguous Ee pair stripped — acceptable false positive)
        assert "thr" in result

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

    def test_deobfuscate_sentence_with_mixed_noise(self):
        """Full sentence with punctuation noise in some words."""
        result = deobfuscate_challenge("wH.hA^aT/t iIsS 32 + 18?")
        assert "what" in result
        assert "is" in result
        assert "32" in result
        assert "18" in result


# ── Real challenge regression tests (from Cherry logs 2026-02-22) ──────────


class TestRealChallengeDeobfuscation:
    """Deobfuscation tests using exact challenge text from production logs."""

    def test_clean_addition_34_plus_22(self):
        """Challenge that succeeded: 'thirty four + twenty two' (07:13 UTC)."""
        raw = (
            "A] DoMiNaNt LoOoBbSsT- ErRr ]ClAw FoRcE Is ThIrTy FoOuR "
            "NoOoToOnS, Um~ AnD A ChAlLeNnGgEr HaS TwEnTy TwOo NoOtToOnS, "
            "WhAt Is ThEiR CoMbInEd FoRcE?"
        )
        clean = deobfuscate_challenge(raw)
        assert "thirty" in clean
        assert "four" in clean
        assert "twenty" in clean
        assert "two" in clean

    def test_clean_addition_35_plus_12(self):
        """Challenge that succeeded: letter-doubled 'thirty five + twelve' (11:58 UTC)."""
        raw = (
            "A] lOo.oBbSsStTeErR ] lAaRrGgEe- clL]aWw ^ eXxEeRrTtSs ] "
            "tHhIiRrTtYy fIiVvEe ] nEeWwOoTtOoNnS ~ wHhIiLlEe ] tHhEe ] "
            "sMmAaLlLlEeRr- clL]aWw ^ eXxEeRrTtSs ] tWwEeLlVvEe < nEeWwOoTtOoNnS"
        )
        clean = deobfuscate_challenge(raw)
        assert "thirty" in clean
        assert "five" in clean
        assert "twelve" in clean

    def test_split_word_twenty_five(self):
        """Challenge that failed: 'T w/eN tY- fIvE' splits 'twenty five'."""
        raw = (
            "ThIs] LoOooBssst-Er S^wImS[ iN aC-iDd WaTeR, ShAkInG aNtEnNaS "
            "aNd MuLtInG; ItS VeLoOociTy Is T w/eN tY- fIvE mE^tErS PeR "
            "sEcOnD, BuT dUrInG a DoMiNaNcE fIgHt It LoOsEs SeVeN oF iTs "
            "SpEeD, So wHaT Is T"
        )
        clean = deobfuscate_challenge(raw)
        # "loses" must survive deobfuscation (subtraction keyword)
        assert "loses" in clean or "lose" in clean
        assert "seven" in clean

    def test_subtraction_slows_by(self):
        """Challenge that failed: 'slows by seven' not detected as subtraction."""
        raw = (
            "A] Lo.BsT-ErRr S^wImS[ aT tWeNtY tHrEe ~ mEtErS / pEr \\ "
            "sEcOnD, Um- SlOwS | bY sEvEn ] wHaTs {tHe} nEw - vElAwCiTeE SpEeD?"
        )
        clean = deobfuscate_challenge(raw)
        assert "twenty" in clean
        assert "seven" in clean
        assert "slows" in clean or "slow" in clean


class TestRealChallengeArithmetic:
    """Arithmetic solver tests using real challenge text.

    After the LLM-first reorder, these test the arithmetic fallback path.
    Correct answers should still work; broken ones should return None
    (deferring to LLM).
    """

    def test_clean_34_plus_22(self):
        """Clean word numbers: thirty four + twenty two = 56."""
        clean = deobfuscate_challenge(
            "A] DoMiNaNt LoOoBbSsT- ErRr ]ClAw FoRcE Is ThIrTy FoOuR "
            "NoOoToOnS, Um~ AnD A ChAlLeNnGgEr HaS TwEnTy TwOo NoOtToOnS, "
            "WhAt Is ThEiR CoMbInEd FoRcE?"
        )
        result = solve_arithmetic(clean)
        assert result == "56.00"

    def test_clean_35_plus_12(self):
        """Letter-doubled: thirty five + twelve = 47."""
        clean = deobfuscate_challenge(
            "A] lOo.oBbSsStTeErR ] lAaRrGgEe- clL]aWw ^ eXxEeRrTtSs ] "
            "tHhIiRrTtYy fIiVvEe ] nEeWwOoTtOoNnS ~ wHhIiLlEe ] tHhEe ] "
            "sMmAaLlLlEeRr- clL]aWw ^ eXxEeRrTtSs ] tWwEeLlVvEe < nEeWwOoTtOoNnS"
        )
        result = solve_arithmetic(clean)
        assert result == "47.00"

    def test_slows_by_detected_as_subtraction(self):
        """'slows by seven' must trigger subtraction, not addition."""
        clean = deobfuscate_challenge(
            "A] Lo.BsT-ErRr S^wImS[ aT tWeNtY tHrEe ~ mEtErS / pEr \\ "
            "sEcOnD, Um- SlOwS | bY sEvEn ] wHaTs {tHe} nEw - vElAwCiTeE SpEeD?"
        )
        result = solve_arithmetic(clean)
        # "twenty thre" → 23, "seven" → 7, "slows" → subtraction → 16
        assert result == "16.00"

    def test_25_plus_7_total_force(self):
        """'twenty five + seven' with 'total force' = addition."""
        clean = deobfuscate_challenge(
            "A] LoB- bS tEr Um] ExErTs^ TwEnTy FiVe NoOoToNs ~, Um ]AnD "
            "ThE/ OtHeR ClA- w ExErTs SeVeN NoOtOnS <>, WhAt] Is ToTaL FoRcE?"
        )
        result = solve_arithmetic(clean)
        assert result == "32.00"

    def test_split_twenty_five_returns_none(self):
        """Split 'T w/eN tY- fIvE' can't be parsed — must return None for LLM."""
        clean = deobfuscate_challenge(
            "ThIs] LoOooBssst-Er S^wImS[ iN aC-iDd WaTeR, ShAkInG aNtEnNaS "
            "aNd MuLtInG; ItS VeLoOociTy Is T w/eN tY- fIvE mE^tErS PeR "
            "sEcOnD, BuT dUrInG a DoMiNaNcE fIgHt It LoOsEs SeVeN oF iTs "
            "SpEeD, So wHaT Is T"
        )
        result = solve_arithmetic(clean)
        # Split word → incomplete extraction → should NOT produce a wrong answer.
        # Acceptable outcomes: None (defers to LLM) or "18.00" (correct)
        assert result is None or result == "18.00"

    def test_split_thirty_five_returns_none(self):
        """Split 'tHiR tY fIvE' can't be parsed — must return None for LLM."""
        clean = deobfuscate_challenge(
            "A] lO b-.StEr ClAw] FoR cE Is^ tHiR tY] fIvE nEu-TonS um~ "
            "aNd| AfTeR- a DoMinAnCe] PiNcH gAiNs^ tWeLvE nEu>ToNs, "
            "wHaT Is< tHe ToTaL- FoRcE?"
        )
        result = solve_arithmetic(clean)
        # Split word → incomplete extraction → should NOT produce a wrong answer.
        assert result is None or result == "47.00"


class TestFuzzyMatchStrictness:
    """Ensure fuzzy matching doesn't produce false positives."""

    def test_thir_does_not_match_thirteen(self):
        """'thir' (fragment of 'thirty') must NOT match 'thirteen'."""
        from overblick.plugins.moltbook.challenge_handler import _ONES

        result = _fuzzy_match("thir", _ONES)
        assert result is None

    def test_ty_does_not_match_twenty(self):
        """'ty' (fragment of 'twenty') must NOT match any number."""
        from overblick.plugins.moltbook.challenge_handler import _TENS, _ONES

        assert _fuzzy_match("ty", _TENS) is None
        assert _fuzzy_match("ty", _ONES) is None

    def test_wen_does_not_match(self):
        """'wen' (fragment of 'twenty') must NOT match any number."""
        from overblick.plugins.moltbook.challenge_handler import _TENS, _ONES

        assert _fuzzy_match("wen", _TENS) is None
        assert _fuzzy_match("wen", _ONES) is None

    def test_thre_matches_three(self):
        """'thre' (deobfuscation artifact) SHOULD match 'three'."""
        from overblick.plugins.moltbook.challenge_handler import _ONES

        result = _fuzzy_match("thre", _ONES)
        assert result is not None
        assert result[1] == 3

    def test_exact_matches_preserved(self):
        """Exact word matches must always work."""
        from overblick.plugins.moltbook.challenge_handler import _TENS, _ONES

        assert _fuzzy_match("twenty", _TENS) == ("twenty", 20)
        assert _fuzzy_match("seven", _ONES) == ("seven", 7)
        assert _fuzzy_match("thirteen", _ONES) == ("thirteen", 13)


class TestSubtractionDetection:
    """Ensure subtraction keywords from real challenges are detected."""

    def test_slows_triggers_subtraction(self):
        """'slows by seven' must detect subtraction."""
        result = solve_arithmetic("speed is twenty three and slows by seven")
        assert result == "16.00"

    def test_loses_triggers_subtraction(self):
        """'loses seven' must detect subtraction."""
        result = solve_arithmetic("velocity is twenty five and loses seven")
        assert result == "18.00"

    def test_drops_triggers_subtraction(self):
        """'drops by five' must detect subtraction."""
        result = solve_arithmetic("force is thirty and drops by five")
        assert result == "25.00"


class TestLLMAnswerValidation:
    """Test validation of LLM answers against arithmetic solver."""

    def test_answers_differ_detects_discrepancy(self):
        """Different numeric answers should be detected."""
        result = PerContentChallengeHandler._answers_differ("30", "40.00")
        assert result is True

    def test_answers_differ_same_numbers(self):
        """Same numbers in different formats should not differ."""
        result = PerContentChallengeHandler._answers_differ("40", "40.00")
        assert result is False

    def test_answers_differ_textual_answers(self):
        """Text answers that are different should be detected."""
        result = PerContentChallengeHandler._answers_differ("hello", "world")
        assert result is True

    def test_answers_differ_same_text(self):
        """Same text answers should not differ."""
        result = PerContentChallengeHandler._answers_differ("answer", "answer")
        assert result is False

    @pytest.mark.asyncio
    async def test_llm_fallback_to_arithmetic_on_discrepancy(self):
        """When LLM answer differs from arithmetic, use arithmetic."""
        session = _make_mock_session(200, '{"success": true}')
        handler = PerContentChallengeHandler(
            llm_client=AsyncMock(),
            http_session=session,
            base_url="https://api.moltbook.test",
        )

        # Mock LLM to return wrong answer "30" for "twenty six + fourteen"
        handler._llm.chat = AsyncMock(return_value={"content": "30"})

        challenge_data = {
            "question": "Lobster claw force is twenty six newtons and gains fourteen more, what is total force?",
            "nonce": "test_nonce",
            "respond_url": "/api/v1/verify",
        }

        result = await handler.solve(challenge_data)

        # Should use arithmetic answer (40.00) not LLM answer (30)
        assert result is not None
        assert handler._stats["challenges_solved"] == 1

        # Verify the SUBMITTED answer was 40.00 (arithmetic), not 30 (LLM)
        call_kwargs = session.post.call_args[1]
        assert call_kwargs["json"]["answer"] == "40.00"
