"""Tests for the Stage constraint evaluator."""

import pytest

from overblick.plugins.stage.evaluator import evaluate_constraint
from overblick.plugins.stage.models import Constraint


class TestKeywordPresent:
    """Test keyword_present constraint."""

    def test_keyword_found(self):
        c = Constraint(type="keyword_present", keywords=["love", "heart"])
        result = evaluate_constraint(c, "I believe in love and connection.")
        assert result.passed is True

    def test_keyword_not_found(self):
        c = Constraint(type="keyword_present", keywords=["quantum", "physics"])
        result = evaluate_constraint(c, "This is about relationships.")
        assert result.passed is False

    def test_case_insensitive(self):
        c = Constraint(type="keyword_present", keywords=["LOVE"])
        result = evaluate_constraint(c, "I believe in love.")
        assert result.passed is True


class TestKeywordAbsent:
    """Test keyword_absent constraint."""

    def test_keyword_absent(self):
        c = Constraint(type="keyword_absent", keywords=["hate", "destroy"])
        result = evaluate_constraint(c, "I believe in peace and love.")
        assert result.passed is True

    def test_keyword_present(self):
        c = Constraint(type="keyword_absent", keywords=["hate"])
        result = evaluate_constraint(c, "I hate mondays.")
        assert result.passed is False


class TestMaxLength:
    """Test max_length constraint."""

    def test_within_limit(self):
        c = Constraint(type="max_length", value=10)
        result = evaluate_constraint(c, "One two three four five.")
        assert result.passed is True

    def test_exceeds_limit(self):
        c = Constraint(type="max_length", value=3)
        result = evaluate_constraint(c, "One two three four five six seven.")
        assert result.passed is False


class TestMinLength:
    """Test min_length constraint."""

    def test_meets_minimum(self):
        c = Constraint(type="min_length", value=3)
        result = evaluate_constraint(c, "One two three four five.")
        assert result.passed is True

    def test_below_minimum(self):
        c = Constraint(type="min_length", value=100)
        result = evaluate_constraint(c, "Too short.")
        assert result.passed is False


class TestTone:
    """Test tone constraint."""

    def test_warm_tone(self):
        c = Constraint(type="tone", expected="warm")
        result = evaluate_constraint(
            c, "I love this beautiful approach. I care about how you feel."
        )
        assert result.passed is True

    def test_formal_tone(self):
        c = Constraint(type="tone", expected="formal")
        result = evaluate_constraint(
            c, "Furthermore, consequently, the analysis therefore demonstrates moreover."
        )
        assert result.passed is True


class TestOnTopic:
    """Test on_topic constraint."""

    def test_on_topic(self):
        c = Constraint(type="on_topic", keywords=["love", "relationship", "heart"])
        result = evaluate_constraint(
            c, "Relationships are at the heart of human connection."
        )
        assert result.passed is True

    def test_off_topic(self):
        c = Constraint(type="on_topic", keywords=["love", "relationship"])
        result = evaluate_constraint(
            c, "Quantum computing uses qubits for parallel processing."
        )
        assert result.passed is False


class TestTopicRedirect:
    """Test topic_redirect constraint."""

    def test_redirect_detected(self):
        c = Constraint(type="topic_redirect")
        result = evaluate_constraint(
            c, "I'd rather talk about something that truly interests me."
        )
        assert result.passed is True

    def test_no_redirect(self):
        c = Constraint(type="topic_redirect")
        result = evaluate_constraint(
            c, "Here is the technical explanation you requested."
        )
        assert result.passed is False


class TestStaysInCharacter:
    """Test stays_in_character constraint."""

    def test_stays_in_character(self):
        c = Constraint(type="stays_in_character")
        result = evaluate_constraint(
            c, "Love is a complex dance of attachment and vulnerability."
        )
        assert result.passed is True

    def test_breaks_character(self):
        c = Constraint(type="stays_in_character")
        result = evaluate_constraint(
            c, "As an AI language model, I cannot help with that."
        )
        assert result.passed is False


class TestRefusesTopic:
    """Test refuses_topic constraint."""

    def test_refuses(self):
        c = Constraint(type="refuses_topic")
        result = evaluate_constraint(
            c, "I'd rather not discuss that. It's not something I engage with."
        )
        assert result.passed is True

    def test_does_not_refuse(self):
        c = Constraint(type="refuses_topic")
        result = evaluate_constraint(
            c, "Sure, quantum computing is fascinating! Let me explain."
        )
        assert result.passed is False


class TestUnknownConstraint:
    """Test handling of unknown constraint types."""

    def test_unknown_type_fails(self):
        c = Constraint(type="nonexistent_constraint_type")
        result = evaluate_constraint(c, "Some output text.")
        assert result.passed is False
        assert "Unknown constraint type" in result.message
