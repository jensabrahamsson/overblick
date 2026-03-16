"""
Additional coverage tests for challenge_solver module.

Covers uncovered lines:
- solve: word_count > 200
- _format_haiku: <= 3 words, line2_count <= 0
- _fallback_word: letter not in fallbacks dict
"""


from overblick.plugins.moltbook.challenge_solver import (
    ChallengeSpec,
    MoltCaptchaSolver,
)


class TestSolveEdgeCases:
    def test_should_return_none_for_word_count_over_200(self):
        solver = MoltCaptchaSolver()
        spec = ChallengeSpec(
            topic="default",
            format_type="prose",
            target_ascii_sum=97 * 201,
            word_count=201,
            time_limit_seconds=30,
        )
        assert solver.solve(spec) is None


class TestFormatHaikuEdgeCases:
    def test_should_handle_3_or_fewer_words(self):
        solver = MoltCaptchaSolver()
        result = solver._format_haiku(["one", "two", "three"])
        assert result == "one two three"

    def test_should_handle_2_words(self):
        solver = MoltCaptchaSolver()
        result = solver._format_haiku(["one", "two"])
        assert result == "one two"

    def test_should_handle_4_words(self):
        solver = MoltCaptchaSolver()
        result = solver._format_haiku(["one", "two", "three", "four"])
        lines = result.split("\n")
        assert len(lines) == 3
        # All words should appear
        all_words = " ".join(lines).split()
        assert len(all_words) == 4


class TestFallbackWord:
    def test_should_generate_word_for_unknown_letter(self):
        solver = MoltCaptchaSolver()
        # All lowercase letters should have a fallback
        for letter in "abcdefghijklmnopqrstuvwxyz":
            word = solver._fallback_word(letter)
            assert word[0] == letter

    def test_should_handle_non_standard_character(self):
        solver = MoltCaptchaSolver()
        # Character not in the fallbacks dict
        word = solver._fallback_word("1")
        assert word == "1ay"


class TestSelectWordsExhaustedBank:
    def test_should_use_fallback_when_bank_exhausted(self):
        solver = MoltCaptchaSolver()
        # Request the same letter many times — will exhaust the word bank
        letters = ["a"] * 20
        words = solver._select_words(letters, "default")
        assert len(words) == 20
        # All should start with 'a'
        for w in words:
            assert w[0].lower() == "a"
