"""
Additional coverage tests for stage evaluator module.

Covers uncovered lines:
- 228-236: _eval_tone with neutral tone (score == 0) and negative score
"""

from overblick.plugins.stage.evaluator import evaluate_constraint
from overblick.plugins.stage.models import Constraint


class TestToneNeutral:
    def test_should_pass_neutral_tone_with_zero_score(self):
        """Lines 228-235: neutral tone with zero score passes."""
        c = Constraint(type="tone", expected="neutral")
        result = evaluate_constraint(c, "This is a plain statement about facts.")
        assert result.passed is True
        assert "neutral" in result.message.lower()

    def test_should_fail_tone_with_negative_score(self):
        """Lines 236-240: negative score fails tone check."""
        c = Constraint(type="tone", expected="warm")
        result = evaluate_constraint(c, "I hate and destroy everything worthless.")
        assert result.passed is False

    def test_should_handle_unknown_tone(self):
        """Unknown tone type falls through with no indicators."""
        c = Constraint(type="tone", expected="mysterious")
        result = evaluate_constraint(c, "Hello world")
        # No indicators found, score=0, not neutral -> fail
        assert result.passed is False

    def test_should_handle_tone_with_no_expected(self):
        """Tone with no expected defaults to neutral."""
        c = Constraint(type="tone")
        result = evaluate_constraint(c, "Plain text.")
        assert result.passed is True  # default neutral, score=0


class TestKeywordPresentWithValue:
    def test_should_use_value_when_no_keywords(self):
        """Uses constraint.value as keyword when keywords list is empty."""
        c = Constraint(type="keyword_present", value="python")
        result = evaluate_constraint(c, "I love python programming.")
        assert result.passed is True


class TestKeywordAbsentWithValue:
    def test_should_use_value_when_no_keywords(self):
        """Uses constraint.value as keyword when keywords list is empty."""
        c = Constraint(type="keyword_absent", value="java")
        result = evaluate_constraint(c, "I love python programming.")
        assert result.passed is True


class TestMaxLengthNoValue:
    def test_should_default_to_500_words(self):
        """max_length defaults to 500 when value is None."""
        c = Constraint(type="max_length")
        result = evaluate_constraint(c, "Short text.")
        assert result.passed is True


class TestMinLengthNoValue:
    def test_should_default_to_50_words(self):
        """min_length defaults to 50 when value is None."""
        c = Constraint(type="min_length")
        result = evaluate_constraint(c, "Short.")
        assert result.passed is False
