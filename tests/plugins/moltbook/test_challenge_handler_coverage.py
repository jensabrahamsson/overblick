"""
Additional tests for challenge_handler.py to achieve 100% coverage.

Covers: _check_challenge_fields non-dict, _solve_with_llm edge cases,
_try_submit strategies, _submit_answer, _submit_answer_no_auth,
_format_answer, _post_with_logging, _extract_time_limit, _record_challenge,
_answers_differ, _audit, set_session, and all solver fallback paths.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.plugins.moltbook.challenge_handler import (
    PerContentChallengeHandler,
)


def _make_handler(**kwargs):
    """Create a PerContentChallengeHandler with defaults."""
    return PerContentChallengeHandler(
        llm_pipeline=kwargs.get("llm_pipeline", AsyncMock()),
        http_session=kwargs.get("http_session"),
        api_key=kwargs.get("api_key", ""),
        base_url=kwargs.get("base_url", ""),
        timeout=kwargs.get("timeout", 45),
        audit_log=kwargs.get("audit_log"),
        engagement_db=kwargs.get("engagement_db"),
    )


def _make_mock_session(status=200, body='{"success": true}'):
    """Create a mock aiohttp session."""
    session = MagicMock()
    mock_response = AsyncMock()
    mock_response.status = status
    mock_response.text = AsyncMock(return_value=body)
    mock_response.json = AsyncMock(return_value={"success": True})
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    session.post = MagicMock(return_value=mock_response)
    return session


class TestCheckChallengeFieldsNonDict:
    def test_non_dict_returns_false(self):
        """Line 190: _check_challenge_fields with non-dict input."""
        handler = _make_handler()
        assert handler._check_challenge_fields("not a dict") is False

    def test_nonce_and_endpoint_without_question(self):
        """Lines 199-200: nonce + endpoint but no question."""
        handler = _make_handler()
        assert handler._check_challenge_fields({"nonce": "a", "submit_url": "/v"}) is True


class TestSolveWithLLM:
    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self):
        """Lines 401-403: LLM chat raises exception."""
        llm = AsyncMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        handler = _make_handler(llm_pipeline=llm)
        result = await handler._solve_with_llm("What is 2+2?", complexity="low")
        assert result is None

    @pytest.mark.asyncio
    async def test_raw_dict_result(self):
        """Lines 416-420: LLM returns raw dict (not PipelineResult)."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": "42"})
        handler = _make_handler(llm_pipeline=llm)
        result = await handler._solve_with_llm("What is 6*7?")
        assert result == "42"

    @pytest.mark.asyncio
    async def test_raw_dict_no_content(self):
        """Lines 418-419: Raw dict with no 'content' key."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"result": "ok"})
        handler = _make_handler(llm_pipeline=llm)
        result = await handler._solve_with_llm("test?")
        assert result is None

    @pytest.mark.asyncio
    async def test_raw_none_result(self):
        """Lines 418: LLM returns None."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=None)
        handler = _make_handler(llm_pipeline=llm)
        result = await handler._solve_with_llm("test?")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_content_returns_none(self):
        """Line 424: Empty content from raw dict LLM result."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value={"content": ""})
        handler = _make_handler(llm_pipeline=llm)
        result = await handler._solve_with_llm("test?")
        assert result is None

    @pytest.mark.asyncio
    async def test_pipeline_result_empty_content(self):
        """Line 424: PipelineResult not blocked but empty content."""
        from overblick.core.llm.pipeline import PipelineResult

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=PipelineResult(content=""))
        handler = _make_handler(llm_pipeline=llm)
        result = await handler._solve_with_llm("test?")
        assert result is None


class TestSolveArithmeticCrossValidation:
    @pytest.mark.asyncio
    async def test_llm_differs_from_arithmetic_negative_trusts_llm(self):
        """Line 301: LLM answer differs from arithmetic, arithmetic is negative."""
        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=MagicMock(blocked=False, content="5"))
        handler = _make_handler(llm_pipeline=llm)
        with patch("overblick.plugins.moltbook.challenge_handler.solve_arithmetic", return_value="-3"):
            result = await handler.solve(
                {"question": "What is five?", "nonce": "abc"},
            )
        # Should trust LLM since arithmetic is negative
        assert handler._stats["challenges_detected"] >= 1

    @pytest.mark.asyncio
    async def test_all_solvers_fail_records_audit(self):
        """Lines 316-328: All solvers fail, audit recorded."""
        from overblick.core.llm.pipeline import PipelineResult, PipelineStage

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=PipelineResult(
            blocked=True, block_reason="fail", block_stage=PipelineStage.PREFLIGHT,
        ))
        audit = MagicMock()
        handler = _make_handler(llm_pipeline=llm, audit_log=audit)
        with patch("overblick.plugins.moltbook.challenge_handler.solve_arithmetic", return_value=None):
            result = await handler.solve(
                {"question": "Impossible question", "nonce": "abc"},
            )
        assert result is None
        assert handler._stats["challenges_failed"] >= 1
        assert audit.log.called


class TestTrySubmit:
    @pytest.mark.asyncio
    async def test_verify_with_challenge_id(self):
        """Line 456: challenge_id included in verify payload."""
        session = _make_mock_session()
        handler = _make_handler(
            http_session=session,
            base_url="https://api.test.com",
            api_key="key",
        )
        result = await handler._try_submit(
            answer="42",
            nonce="n1",
            challenge_data={"challenge_id": "ch1"},
            endpoint=None,
            original_endpoint=None,
            original_payload=None,
        )
        assert result is not None
        # Verify challenge_id was in payload
        call_kwargs = session.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["challenge_id"] == "ch1"

    @pytest.mark.asyncio
    async def test_explicit_endpoint_strategy(self):
        """Lines 478-490: Strategy 2 — explicit submit endpoint."""
        session = _make_mock_session()
        handler = _make_handler(
            http_session=session,
            base_url="https://api.test.com",
            api_key="key",
        )
        # Make strategy 1 fail
        fail_resp = AsyncMock()
        fail_resp.status = 400
        fail_resp.text = AsyncMock(return_value="error")
        fail_resp.headers = {}
        fail_resp.__aenter__ = AsyncMock(return_value=fail_resp)
        fail_resp.__aexit__ = AsyncMock(return_value=False)

        ok_resp = AsyncMock()
        ok_resp.status = 200
        ok_resp.text = AsyncMock(return_value='{"success": true}')
        ok_resp.json = AsyncMock(return_value={"success": True})
        ok_resp.headers = {}
        ok_resp.__aenter__ = AsyncMock(return_value=ok_resp)
        ok_resp.__aexit__ = AsyncMock(return_value=False)

        session.post = MagicMock(side_effect=[fail_resp, ok_resp])

        result = await handler._try_submit(
            answer="42",
            nonce="n1",
            challenge_data={},
            endpoint="/custom-verify",
            original_endpoint=None,
            original_payload=None,
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_retry_original_strategy(self):
        """Lines 492-515: Strategy 3 — retry original POST with verification."""
        session = _make_mock_session()
        handler = _make_handler(
            http_session=session,
            base_url="https://api.test.com",
            api_key="key",
        )
        # Strategy 1 fails
        fail_resp = AsyncMock()
        fail_resp.status = 400
        fail_resp.text = AsyncMock(return_value="error")
        fail_resp.headers = {}
        fail_resp.__aenter__ = AsyncMock(return_value=fail_resp)
        fail_resp.__aexit__ = AsyncMock(return_value=False)

        ok_resp = AsyncMock()
        ok_resp.status = 200
        ok_resp.text = AsyncMock(return_value='{"success": true}')
        ok_resp.headers = {}
        ok_resp.__aenter__ = AsyncMock(return_value=ok_resp)
        ok_resp.__aexit__ = AsyncMock(return_value=False)

        session.post = MagicMock(side_effect=[fail_resp, ok_resp])

        result = await handler._try_submit(
            answer="42",
            nonce="n1",
            challenge_data={},
            endpoint=None,
            original_endpoint="/posts/123/comments",
            original_payload={"content": "Hello"},
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_all_strategies_fail(self):
        """Lines 517-531: All submit strategies fail."""
        session = _make_mock_session(status=400, body='{"error": "failed"}')
        audit = MagicMock()
        handler = _make_handler(
            http_session=session,
            base_url="https://api.test.com",
            api_key="key",
            audit_log=audit,
        )
        # Override to fail
        fail_resp = AsyncMock()
        fail_resp.status = 400
        fail_resp.text = AsyncMock(return_value="error")
        fail_resp.headers = {}
        fail_resp.__aenter__ = AsyncMock(return_value=fail_resp)
        fail_resp.__aexit__ = AsyncMock(return_value=False)
        session.post = MagicMock(return_value=fail_resp)

        result = await handler._try_submit(
            answer="42",
            nonce=None,
            challenge_data={},
            endpoint=None,
            original_endpoint="/test",
            original_payload={},
        )
        assert result is None


class TestFormatAnswer:
    def test_numeric_answer_formatted(self):
        assert PerContentChallengeHandler._format_answer("42") == "42.00"

    def test_float_answer_formatted(self):
        assert PerContentChallengeHandler._format_answer("3.14159") == "3.14"

    def test_non_numeric_answer_unchanged(self):
        assert PerContentChallengeHandler._format_answer("spider") == "spider"

    def test_nan_answer_unchanged(self):
        assert PerContentChallengeHandler._format_answer("nan") == "nan"


class TestAnswersDiffer:
    def test_same_numeric_answers(self):
        assert PerContentChallengeHandler._answers_differ("42", "42.00") is False

    def test_different_numeric_answers(self):
        assert PerContentChallengeHandler._answers_differ("42", "43") is True

    def test_non_numeric_same(self):
        assert PerContentChallengeHandler._answers_differ("cat", "cat") is False

    def test_non_numeric_different(self):
        assert PerContentChallengeHandler._answers_differ("cat", "dog") is True


class TestPostWithLogging:
    @pytest.mark.asyncio
    async def test_no_session_returns_none(self):
        """Line 557: No session available."""
        handler = _make_handler()
        result = await handler._post_with_logging("/test", {}, "test")
        assert result is None

    @pytest.mark.asyncio
    async def test_with_api_key(self):
        """Line 561: API key added to headers."""
        session = _make_mock_session()
        handler = _make_handler(http_session=session, api_key="mykey")
        result = await handler._post_with_logging("/test", {}, "test")
        assert result is not None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        """Line 584: HTTP >= 400 returns None."""
        session = _make_mock_session(status=400)
        handler = _make_handler(http_session=session)
        result = await handler._post_with_logging("/test", {}, "test")
        assert result is None

    @pytest.mark.asyncio
    async def test_json_parse_error_returns_raw(self):
        """Lines 590-594: JSON parse error returns raw response."""
        resp = AsyncMock()
        resp.status = 200
        resp.text = AsyncMock(return_value="not json")
        resp.headers = {}
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        handler = _make_handler(http_session=session)
        result = await handler._post_with_logging("/test", {}, "test")
        assert result["raw_response"] == "not json"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """Lines 595-597: Exception in POST returns None."""
        session = MagicMock()
        session.post = MagicMock(side_effect=RuntimeError("conn fail"))
        handler = _make_handler(http_session=session)
        result = await handler._post_with_logging("/test", {}, "test")
        assert result is None

    @pytest.mark.asyncio
    async def test_absolute_url(self):
        """Line 565: URL starts with http."""
        session = _make_mock_session()
        handler = _make_handler(http_session=session, base_url="https://api.test.com")
        result = await handler._post_with_logging("https://other.com/test", {}, "test")
        assert result is not None


class TestSubmitAnswer:
    @pytest.mark.asyncio
    async def test_no_session_returns_none(self):
        """Lines 650-651: No session."""
        handler = _make_handler()
        result = await handler._submit_answer("/verify", "42", "n1", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_absolute_url_same_base(self):
        """Lines 654-667: Absolute URL matching base_url."""
        session = _make_mock_session()
        handler = _make_handler(
            http_session=session,
            base_url="https://api.test.com",
            api_key="key",
        )
        result = await handler._submit_answer(
            "https://api.test.com/verify", "42", "n1", {"challenge_id": "ch1"}
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_absolute_url_different_base_no_auth(self):
        """Lines 654-665: Absolute URL NOT matching base_url → no auth."""
        session = _make_mock_session()
        handler = _make_handler(
            http_session=session,
            base_url="https://api.test.com",
            api_key="key",
        )
        result = await handler._submit_answer(
            "https://evil.com/verify", "42", None, {}
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_absolute_url_no_base_url(self):
        """Line 658: No base_url configured, absolute URL → no auth."""
        session = _make_mock_session()
        handler = _make_handler(http_session=session)
        result = await handler._submit_answer(
            "https://moltbook.com/verify", "42", None, {}
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_relative_url(self):
        """Line 667: Relative URL prepended with base_url."""
        session = _make_mock_session()
        handler = _make_handler(
            http_session=session,
            base_url="https://api.test.com",
        )
        result = await handler._submit_answer("/verify", "42", None, {})
        assert result is not None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        """Lines 693-695: HTTP >= 400 returns None."""
        session = _make_mock_session(status=400)
        handler = _make_handler(
            http_session=session,
            base_url="https://api.test.com",
        )
        result = await handler._submit_answer("/verify", "42", "n1", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_json_parse_error_returns_raw(self):
        """Lines 699-700: JSON parse error returns raw response."""
        resp = AsyncMock()
        resp.status = 200
        resp.text = AsyncMock(return_value="not json")
        resp.json = AsyncMock(side_effect=Exception("parse error"))
        resp.headers = {}
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        handler = _make_handler(
            http_session=session,
            base_url="https://api.test.com",
        )
        result = await handler._submit_answer("/verify", "42", None, {})
        assert result["raw_response"] == "not json"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """Lines 702-704: Exception returns None."""
        session = MagicMock()
        session.post = MagicMock(side_effect=RuntimeError("conn fail"))
        handler = _make_handler(
            http_session=session,
            base_url="https://api.test.com",
        )
        result = await handler._submit_answer("/verify", "42", None, {})
        assert result is None


class TestSubmitAnswerNoAuth:
    @pytest.mark.asyncio
    async def test_no_session_returns_none(self):
        """Lines 722-723: No session."""
        handler = _make_handler()
        result = await handler._submit_answer_no_auth("https://x.com/v", "42", None, {})
        assert result is None

    @pytest.mark.asyncio
    async def test_success(self):
        """Lines 725-738: Successful no-auth submission."""
        session = _make_mock_session()
        handler = _make_handler(http_session=session)
        result = await handler._submit_answer_no_auth(
            "https://external.com/verify", "42", "n1", {"challenge_id": "ch1"}
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        """Lines 733-734: HTTP >= 400."""
        session = _make_mock_session(status=400)
        handler = _make_handler(http_session=session)
        result = await handler._submit_answer_no_auth(
            "https://x.com/v", "42", None, {}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_json_parse_error_returns_raw(self):
        """Lines 737-738: JSON parse fails."""
        resp = AsyncMock()
        resp.status = 200
        resp.text = AsyncMock(return_value="not json")
        resp.json = AsyncMock(side_effect=Exception("parse"))
        resp.headers = {}
        resp.__aenter__ = AsyncMock(return_value=resp)
        resp.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=resp)
        handler = _make_handler(http_session=session)
        result = await handler._submit_answer_no_auth(
            "https://x.com/v", "42", None, {}
        )
        assert result["raw_response"] == "not json"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        """Lines 740-741: Exception returns None."""
        session = MagicMock()
        session.post = MagicMock(side_effect=RuntimeError("err"))
        handler = _make_handler(http_session=session)
        result = await handler._submit_answer_no_auth(
            "https://x.com/v", "42", None, {}
        )
        assert result is None


class TestSetSession:
    def test_set_session(self):
        """Lines 743-745: set_session stores session."""
        handler = _make_handler()
        session = MagicMock()
        handler.set_session(session)
        assert handler._session is session


class TestExtractTimeLimit:
    def test_valid_time_limit(self):
        """Lines 636-637: Valid time limit extracted."""
        handler = _make_handler()
        result = handler._extract_time_limit({"time_limit": "300"})
        assert result == 300

    def test_invalid_time_limit(self):
        """Lines 638-639: Invalid time limit returns None."""
        handler = _make_handler()
        result = handler._extract_time_limit({"time_limit": "not-a-number"})
        assert result is None

    def test_no_time_limit(self):
        """Line 640: No time limit field."""
        handler = _make_handler()
        result = handler._extract_time_limit({"other": "stuff"})
        assert result is None


class TestAudit:
    def test_audit_logs_when_available(self):
        audit = MagicMock()
        handler = _make_handler(audit_log=audit)
        handler._audit("test_action", {"key": "val"})
        audit.log.assert_called_once_with(action="test_action", details={"key": "val"})

    def test_audit_error_suppressed(self):
        """Lines 758-759: Audit log error suppressed."""
        audit = MagicMock()
        audit.log = MagicMock(side_effect=RuntimeError("audit fail"))
        handler = _make_handler(audit_log=audit)
        handler._audit("test", {})  # Should not raise

    def test_no_audit_log(self):
        handler = _make_handler()
        handler._audit("test", {})  # Should not raise


class TestRecordChallenge:
    @pytest.mark.asyncio
    async def test_no_engagement_db(self):
        """Line 775: No engagement DB."""
        handler = _make_handler()
        await handler._record_challenge(
            challenge_id=None, question_raw=None, question_clean=None,
            answer=None, solver=None, correct=False, endpoint=None,
            duration_ms=0, http_status=None, error=None,
        )

    @pytest.mark.asyncio
    async def test_engagement_db_error(self):
        """Lines 790-791: DB record error suppressed."""
        db = AsyncMock()
        db.record_challenge = AsyncMock(side_effect=RuntimeError("db fail"))
        handler = _make_handler(engagement_db=db)
        await handler._record_challenge(
            challenge_id="ch1", question_raw="q", question_clean="q",
            answer="a", solver="llm", correct=True, endpoint="/v",
            duration_ms=100, http_status=200, error=None,
        )

    @pytest.mark.asyncio
    async def test_engagement_db_success(self):
        db = AsyncMock()
        db.record_challenge = AsyncMock()
        handler = _make_handler(engagement_db=db)
        await handler._record_challenge(
            challenge_id="ch1", question_raw="q", question_clean="q",
            answer="a", solver="llm", correct=True, endpoint="/v",
            duration_ms=100, http_status=200, error=None,
        )
        db.record_challenge.assert_called_once()


class TestTraversePath:
    def test_non_dict_in_path(self):
        """Line 208: Non-dict encountered in path."""
        result = PerContentChallengeHandler._traverse_path(
            {"a": "not_a_dict"}, ("a", "b")
        )
        assert result is None

    def test_missing_key(self):
        result = PerContentChallengeHandler._traverse_path({"a": {}}, ("a", "b"))
        assert result is None

    def test_final_value_not_dict(self):
        result = PerContentChallengeHandler._traverse_path(
            {"a": {"b": "string"}}, ("a", "b")
        )
        assert result is None
