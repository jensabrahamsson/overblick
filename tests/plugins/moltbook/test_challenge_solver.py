"""Tests for MoltCaptcha solver."""

from overblick.plugins.moltbook.challenge_solver import MoltCaptchaSolver


class TestMoltCaptchaSolver:
    def test_solver_creates(self):
        solver = MoltCaptchaSolver()
        assert solver is not None

    def test_select_words_from_topic_bank(self):
        solver = MoltCaptchaSolver()
        # The solver should select words from topic word banks
        words = solver._select_words(["a", "b", "c"], "default")
        assert len(words) == 3
        assert words[0][0] == "a"
        assert words[1][0] == "b"
        assert words[2][0] == "c"
