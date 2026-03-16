"""
Additional coverage tests for stylometry module.

Covers uncovered lines:
- 38: analyze_text returns early with word_count=0 (only punctuation)
- 160: _compute_formality with empty word list
"""

from overblick.plugins.compass.stylometry import _compute_formality, analyze_text


class TestAnalyzeTextEdgeCases:
    def test_should_return_zero_word_count_for_punctuation_only(self):
        """Line 38: text with only punctuation returns word_count=0."""
        metrics = analyze_text("... !!! ???")
        assert metrics.word_count == 0

    def test_should_handle_whitespace_only(self):
        """Empty/whitespace text returns defaults."""
        metrics = analyze_text("   ")
        assert metrics.word_count == 0


class TestComputeDriftScoreZeroStd:
    def test_should_handle_zero_std_dev(self):
        """Line 124: z_score = 0.0 when std == 0."""
        from overblick.plugins.compass.models import StyleMetrics
        from overblick.plugins.compass.stylometry import compute_drift_score

        baseline = StyleMetrics(avg_sentence_length=10.0, avg_word_length=5.0)
        current = StyleMetrics(avg_sentence_length=15.0, avg_word_length=5.0)

        # Provide std_dev of 0 for some dimensions
        std_devs = {
            "avg_sentence_length": 0.0,  # This triggers line 124
            "avg_word_length": 1.0,
        }
        score, _drifted = compute_drift_score(current, baseline, std_devs)
        assert score >= 0


class TestComputeFormalityEmpty:
    def test_should_return_half_for_empty_words(self):
        """Line 160: empty word list returns 0.5."""
        result = _compute_formality([], 0.0)
        assert result == 0.5
