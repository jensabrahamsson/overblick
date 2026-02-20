"""Tests for stylometric analysis functions."""

import pytest

from overblick.plugins.compass.stylometry import analyze_text, compute_drift_score
from overblick.plugins.compass.models import StyleMetrics


class TestAnalyzeText:
    """Test the stylometric analysis function."""

    def test_empty_text_returns_defaults(self):
        """Empty text returns default metrics."""
        metrics = analyze_text("")
        assert metrics.word_count == 0

    def test_basic_text(self):
        """Basic text analysis works correctly."""
        text = "This is a simple test. It has two sentences."
        metrics = analyze_text(text)
        assert metrics.word_count == 9
        assert metrics.avg_sentence_length > 0
        assert metrics.avg_word_length > 0
        assert metrics.vocabulary_richness > 0

    def test_question_ratio(self):
        """Detects questions correctly."""
        text = "Is this a question? Yes it is. Another question? Sure."
        metrics = analyze_text(text)
        assert metrics.question_ratio > 0
        assert metrics.question_ratio == pytest.approx(0.5, abs=0.1)

    def test_exclamation_ratio(self):
        """Detects exclamations correctly."""
        text = "Wow! Amazing! Incredible! Cool."
        metrics = analyze_text(text)
        assert metrics.exclamation_ratio > 0

    def test_formal_text(self):
        """Formal text scores higher on formality."""
        formal = (
            "Furthermore, the methodology demonstrates significant correlation "
            "between the observed phenomena and theoretical predictions."
        )
        informal = (
            "lol yeah so like i'm gonna do the thing and it's gonna be awesome"
        )
        formal_metrics = analyze_text(formal)
        informal_metrics = analyze_text(informal)
        assert formal_metrics.formality_score > informal_metrics.formality_score

    def test_vocabulary_richness(self):
        """Repetitive text has lower vocabulary richness."""
        varied = "The quick brown fox jumps over the lazy dog near the river."
        repetitive = "the the the the the the the the the the"
        varied_metrics = analyze_text(varied)
        rep_metrics = analyze_text(repetitive)
        assert varied_metrics.vocabulary_richness > rep_metrics.vocabulary_richness

    def test_long_sentences(self):
        """Text with long sentences has higher avg_sentence_length."""
        long_sentences = (
            "This is a very long sentence that goes on and on with many words "
            "and clauses and phrases that make it quite extensive in nature."
        )
        short_sentences = "Short. Very short. Tiny. Brief. Quick."
        long_metrics = analyze_text(long_sentences)
        short_metrics = analyze_text(short_sentences)
        assert long_metrics.avg_sentence_length > short_metrics.avg_sentence_length


class TestComputeDriftScore:
    """Test the drift score computation."""

    def test_identical_metrics_zero_drift(self):
        """Identical metrics produce zero drift."""
        metrics = StyleMetrics(
            avg_sentence_length=15.0,
            avg_word_length=5.0,
            vocabulary_richness=0.6,
            punctuation_frequency=10.0,
            question_ratio=0.1,
            exclamation_ratio=0.05,
            comma_frequency=5.0,
            formality_score=0.5,
        )
        score, drifted = compute_drift_score(metrics, metrics)
        assert score == 0.0
        assert len(drifted) == 0

    def test_different_metrics_positive_drift(self):
        """Different metrics produce positive drift score."""
        baseline = StyleMetrics(
            avg_sentence_length=15.0,
            avg_word_length=5.0,
            vocabulary_richness=0.6,
            formality_score=0.7,
        )
        current = StyleMetrics(
            avg_sentence_length=5.0,
            avg_word_length=3.0,
            vocabulary_richness=0.3,
            formality_score=0.2,
        )
        score, drifted = compute_drift_score(current, baseline)
        assert score > 0
        assert len(drifted) > 0

    def test_std_devs_affect_scoring(self):
        """Standard deviations affect how drift is scored."""
        baseline = StyleMetrics(avg_sentence_length=15.0, formality_score=0.5)
        current = StyleMetrics(avg_sentence_length=20.0, formality_score=0.5)

        # With tight std dev, same diff is more significant
        tight = {"avg_sentence_length": 1.0}
        loose = {"avg_sentence_length": 10.0}

        score_tight, _ = compute_drift_score(current, baseline, tight)
        score_loose, _ = compute_drift_score(current, baseline, loose)
        assert score_tight > score_loose
