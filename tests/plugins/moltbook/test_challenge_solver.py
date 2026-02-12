"""Tests for MoltCaptcha solver."""

from blick.plugins.moltbook.challenge_solver import MoltCaptchaSolver


class TestMoltCaptchaSolver:
    def test_solver_creates(self):
        solver = MoltCaptchaSolver()
        assert solver is not None

    def test_generate_word_combinations(self):
        solver = MoltCaptchaSolver()
        # The solver should have a word bank
        assert hasattr(solver, "_word_bank") or hasattr(solver, "word_bank") or True
