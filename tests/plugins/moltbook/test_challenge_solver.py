"""Tests for MoltCaptcha solver.

Comprehensive test suite covering:
- Challenge text parsing (standard, haiku, missing fields)
- End-to-end solving (ASCII sum, word count, boundaries)
- Letter combination finding (basic, edge cases)
- is_challenge_text() detection
- Plugin integration (challenge detection in replies and feed)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from overblick.plugins.moltbook.challenge_solver import (
    ChallengeSpec,
    MoltCaptchaSolver,
    is_challenge_text,
)


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def solver():
    return MoltCaptchaSolver()


STANDARD_CHALLENGE = (
    "MOLTCAPTCHA CHALLENGE\n"
    "Hey @Cherry_Tantolunden, prove you're an AI!\n"
    "Generate text about crypto with ASCII sum of first letters = 500\n"
    "Use exactly 5 words. Time limit: 15 seconds.\n"
    "Nonce: abc123def"
)

HAIKU_CHALLENGE = (
    "MOLTCAPTCHA CHALLENGE\n"
    "@TestAgent verification challenge:\n"
    "Write a haiku about nature.\n"
    "ASCII sum of first letters = 315\n"
    "3 words. Time limit: 10s."
)


# ── Parsing Tests ────────────────────────────────────────────────────────

class TestParseChallenge:
    def test_parse_standard_challenge(self, solver):
        spec = solver.parse_challenge(STANDARD_CHALLENGE)
        assert spec is not None
        assert spec.target_ascii_sum == 500
        assert spec.word_count == 5
        assert spec.time_limit_seconds == 15
        assert spec.nonce == "abc123def"
        assert spec.topic == "crypto"

    def test_parse_challenge_with_haiku_format(self, solver):
        spec = solver.parse_challenge(HAIKU_CHALLENGE)
        assert spec is not None
        assert spec.format_type == "haiku"
        assert spec.target_ascii_sum == 315
        assert spec.word_count == 3

    def test_parse_challenge_missing_ascii_sum(self, solver):
        text = "MOLTCAPTCHA CHALLENGE\nGenerate 5 words about nature."
        result = solver.parse_challenge(text)
        assert result is None

    def test_parse_challenge_missing_word_count(self, solver):
        text = "MOLTCAPTCHA CHALLENGE\nASCII sum of first letters = 500."
        result = solver.parse_challenge(text)
        assert result is None

    def test_parse_challenge_no_header(self, solver):
        text = "Generate text with ASCII sum = 500, 5 words about crypto."
        result = solver.parse_challenge(text)
        assert result is None

    def test_parse_extracts_nonce(self, solver):
        spec = solver.parse_challenge(STANDARD_CHALLENGE)
        assert spec is not None
        assert spec.nonce == "abc123def"

    def test_parse_extracts_time_limit(self, solver):
        spec = solver.parse_challenge(STANDARD_CHALLENGE)
        assert spec is not None
        assert spec.time_limit_seconds == 15

    def test_parse_default_time_limit(self, solver):
        text = (
            "MOLTCAPTCHA CHALLENGE\n"
            "ASCII sum of first letters = 300\n"
            "3 words about ai."
        )
        spec = solver.parse_challenge(text)
        assert spec is not None
        assert spec.time_limit_seconds == 30  # Default

    def test_parse_default_format(self, solver):
        text = (
            "MOLTCAPTCHA CHALLENGE\n"
            "ASCII sum of first letters = 300\n"
            "3 words about ai."
        )
        spec = solver.parse_challenge(text)
        assert spec is not None
        assert spec.format_type == "prose"


# ── Solver Tests (End-to-End) ────────────────────────────────────────────

class TestSolve:
    def test_solve_verifies_ascii_sum(self, solver):
        spec = ChallengeSpec(
            topic="default",
            format_type="prose",
            target_ascii_sum=500,
            word_count=5,
            time_limit_seconds=30,
        )
        result = solver.solve(spec)
        assert result is not None

        words = result.split()
        ascii_sum = sum(ord(w[0].lower()) for w in words)
        assert ascii_sum == 500

    def test_solve_verifies_word_count(self, solver):
        spec = ChallengeSpec(
            topic="default",
            format_type="prose",
            target_ascii_sum=500,
            word_count=5,
            time_limit_seconds=30,
        )
        result = solver.solve(spec)
        assert result is not None
        assert len(result.split()) == 5

    def test_solve_impossible_low_sum(self, solver):
        """Target below minimum possible (word_count * 97) -> None."""
        spec = ChallengeSpec(
            topic="default",
            format_type="prose",
            target_ascii_sum=96,  # Less than 1 * 97
            word_count=1,
            time_limit_seconds=30,
        )
        result = solver.solve(spec)
        assert result is None

    def test_solve_impossible_high_sum(self, solver):
        """Target above maximum possible (word_count * 122) -> None."""
        spec = ChallengeSpec(
            topic="default",
            format_type="prose",
            target_ascii_sum=123,  # More than 1 * 122
            word_count=1,
            time_limit_seconds=30,
        )
        result = solver.solve(spec)
        assert result is None

    def test_solve_boundary_all_a(self, solver):
        """Target = word_count * 97 (all 'a' words)."""
        spec = ChallengeSpec(
            topic="default",
            format_type="prose",
            target_ascii_sum=97 * 3,  # 291
            word_count=3,
            time_limit_seconds=30,
        )
        result = solver.solve(spec)
        assert result is not None
        words = result.split()
        assert len(words) == 3
        for w in words:
            assert w[0].lower() == "a"

    def test_solve_boundary_all_z(self, solver):
        """Target = word_count * 122 (all 'z' words)."""
        spec = ChallengeSpec(
            topic="default",
            format_type="prose",
            target_ascii_sum=122 * 3,  # 366
            word_count=3,
            time_limit_seconds=30,
        )
        result = solver.solve(spec)
        assert result is not None
        words = result.split()
        assert len(words) == 3
        for w in words:
            assert w[0].lower() == "z"

    def test_solve_haiku_format(self, solver):
        """Haiku output has 3 lines."""
        spec = ChallengeSpec(
            topic="nature",
            format_type="haiku",
            target_ascii_sum=97 * 6 + 50,  # 632
            word_count=6,
            time_limit_seconds=30,
        )
        result = solver.solve(spec)
        assert result is not None
        lines = result.strip().split("\n")
        assert len(lines) == 3

    def test_solve_includes_nonce(self, solver):
        """Nonce is appended to the solution."""
        spec = ChallengeSpec(
            topic="default",
            format_type="prose",
            target_ascii_sum=97 * 2,  # 194
            word_count=2,
            time_limit_seconds=30,
            nonce="deadbeef42",
        )
        result = solver.solve(spec)
        assert result is not None
        assert "Nonce: deadbeef42" in result

    def test_solve_invalid_word_count(self, solver):
        spec = ChallengeSpec(
            topic="default",
            format_type="prose",
            target_ascii_sum=500,
            word_count=0,
            time_limit_seconds=30,
        )
        assert solver.solve(spec) is None

    def test_solve_negative_ascii_sum(self, solver):
        spec = ChallengeSpec(
            topic="default",
            format_type="prose",
            target_ascii_sum=-1,
            word_count=3,
            time_limit_seconds=30,
        )
        assert solver.solve(spec) is None


# ── Letter Combination Tests ─────────────────────────────────────────────

class TestFindLetterCombination:
    def test_find_letter_combination_basic(self, solver):
        letters = solver._find_letter_combination(300, 3)
        assert letters is not None
        assert len(letters) == 3
        assert sum(ord(c) for c in letters) == 300

    def test_find_letter_combination_edge_all_a(self, solver):
        letters = solver._find_letter_combination(97 * 5, 5)
        assert letters is not None
        assert all(c == "a" for c in letters)

    def test_find_letter_combination_edge_all_z(self, solver):
        letters = solver._find_letter_combination(122 * 5, 5)
        assert letters is not None
        assert all(c == "z" for c in letters)

    def test_find_letter_combination_impossible_low(self, solver):
        result = solver._find_letter_combination(96, 1)
        assert result is None

    def test_find_letter_combination_impossible_high(self, solver):
        result = solver._find_letter_combination(123, 1)
        assert result is None

    def test_find_letter_combination_single_letter(self, solver):
        letters = solver._find_letter_combination(109, 1)
        assert letters == ["m"]  # 109 = ord('m')


# ── Word Selection Tests ─────────────────────────────────────────────────

class TestSelectWords:
    def test_select_words_from_topic_bank(self):
        solver = MoltCaptchaSolver()
        words = solver._select_words(["a", "b", "c"], "default")
        assert len(words) == 3
        assert words[0][0] == "a"
        assert words[1][0] == "b"
        assert words[2][0] == "c"

    def test_select_words_fallback_for_missing_letter(self):
        solver = MoltCaptchaSolver()
        # 'x' has a fallback word "xor"
        words = solver._select_words(["x"], "default")
        assert len(words) == 1
        assert words[0][0] == "x"

    def test_select_words_with_topic(self):
        solver = MoltCaptchaSolver()
        words = solver._select_words(["b", "c"], "crypto")
        assert len(words) == 2
        assert words[0][0] == "b"
        assert words[1][0] == "c"


# ── is_challenge_text Tests ──────────────────────────────────────────────

class TestIsChallengeText:
    def test_detects_challenge_with_agent_name(self):
        text = STANDARD_CHALLENGE
        assert is_challenge_text(text, "Cherry_Tantolunden") is True

    def test_detects_challenge_with_at_mention(self):
        text = (
            "MOLTCAPTCHA CHALLENGE\n"
            "@TestBot prove you're not human!\n"
            "ASCII sum of first letters = 300\n"
            "3 words."
        )
        assert is_challenge_text(text, "TestBot") is True

    def test_ignores_challenge_for_other_agent(self):
        text = STANDARD_CHALLENGE  # Directed at Cherry_Tantolunden
        assert is_challenge_text(text, "OtherAgent") is False

    def test_ignores_normal_text(self):
        text = "Hey everyone, what a beautiful day! Let's talk about crypto."
        assert is_challenge_text(text, "Cherry") is False

    def test_empty_text(self):
        assert is_challenge_text("", "Cherry") is False

    def test_none_text(self):
        assert is_challenge_text(None, "Cherry") is False

    def test_case_insensitive_agent_name(self):
        text = (
            "MOLTCAPTCHA CHALLENGE\n"
            "Hey CHERRY, verification challenge for you!\n"
            "ASCII sum of first letters = 300\n"
            "3 words."
        )
        assert is_challenge_text(text, "cherry") is True

    def test_alternative_challenge_pattern(self):
        text = (
            "Hey @TestAgent, prove you're an AI!\n"
            "ASCII sum first letters = 200\n"
            "2 words."
        )
        assert is_challenge_text(text, "TestAgent") is True


# ── Plugin Integration Tests ─────────────────────────────────────────────

class TestPluginIntegration:
    """Test MoltCaptcha integration in MoltbookPlugin."""

    @pytest.fixture
    def mock_ctx(self):
        """Minimal mock PluginContext."""
        ctx = MagicMock()
        ctx.identity.name = "Cherry"
        ctx.identity.raw_config = {"agent_name": "Cherry_Tantolunden"}
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        ctx.engagement_db.mark_reply_processed = AsyncMock()
        ctx.audit_log.log = MagicMock()
        return ctx

    @pytest.fixture
    def mock_client(self):
        client = AsyncMock()
        client.create_comment = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_challenge_detected_in_reply(self, mock_ctx, mock_client):
        """Challenge in comment triggers solver."""
        from overblick.plugins.moltbook.plugin import MoltbookPlugin

        plugin = MoltbookPlugin(mock_ctx)
        plugin._client = mock_client

        # Create a mock comment with a challenge
        comment = MagicMock()
        comment.id = "comment-1"
        comment.content = STANDARD_CHALLENGE
        comment.agent_name = "Challenger"

        # Test _handle_moltcaptcha
        await plugin._handle_moltcaptcha("post-1", comment)

        # Verify comment was posted (solver should succeed)
        mock_client.create_comment.assert_called_once()
        call_args = mock_client.create_comment.call_args
        assert call_args[0][0] == "post-1"  # post_id
        assert call_args[1].get("parent_id") == "comment-1"  # reply to comment

    @pytest.mark.asyncio
    async def test_challenge_solved_and_replied(self, mock_ctx, mock_client):
        """Solution is posted and audit log is written."""
        from overblick.plugins.moltbook.plugin import MoltbookPlugin

        plugin = MoltbookPlugin(mock_ctx)
        plugin._client = mock_client

        # Build a solvable challenge
        challenge_text = (
            "MOLTCAPTCHA CHALLENGE\n"
            "@Cherry_Tantolunden prove you're an AI!\n"
            "ASCII sum of first letters = 500\n"
            "5 words about default."
        )
        comment = MagicMock()
        comment.id = "comment-2"
        comment.content = challenge_text
        comment.agent_name = "Verifier"

        await plugin._handle_moltcaptcha("post-2", comment)

        # Verify solution was posted
        assert mock_client.create_comment.called
        solution = mock_client.create_comment.call_args[0][1]
        words = solution.split()
        assert len(words) == 5
        assert sum(ord(w[0].lower()) for w in words) == 500

        # Verify audit log
        mock_ctx.audit_log.log.assert_called_once_with(
            action="moltcaptcha_solved",
            details={"post_id": "post-2", "source_id": "comment-2"},
        )

    @pytest.mark.asyncio
    async def test_non_challenge_comment_ignored(self, mock_ctx):
        """Normal comments go through normal flow (not challenge solver)."""
        text = "I really liked your post about AI!"
        agent_name = mock_ctx.identity.raw_config["agent_name"]
        assert is_challenge_text(text, agent_name) is False

    @pytest.mark.asyncio
    async def test_unparseable_challenge_logs_warning(self, mock_ctx, mock_client):
        """Challenge that can't be parsed logs warning, doesn't post."""
        from overblick.plugins.moltbook.plugin import MoltbookPlugin

        plugin = MoltbookPlugin(mock_ctx)
        plugin._client = mock_client

        # Challenge header but missing required fields
        comment = MagicMock()
        comment.id = "comment-3"
        comment.content = "MOLTCAPTCHA CHALLENGE\nGood luck!"
        comment.agent_name = "Troll"

        await plugin._handle_moltcaptcha("post-3", comment)

        # No comment should be posted
        mock_client.create_comment.assert_not_called()
