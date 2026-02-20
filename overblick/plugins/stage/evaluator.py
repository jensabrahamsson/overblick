"""
Constraint evaluation engine for the Stage plugin.

Evaluates LLM output against behavioral constraints defined in
scenario YAML files.
"""

import logging
import re

from .models import Constraint, ConstraintResult

logger = logging.getLogger(__name__)


def evaluate_constraint(constraint: Constraint, output: str) -> ConstraintResult:
    """
    Evaluate a single constraint against LLM output.

    Args:
        constraint: The constraint to check.
        output: The LLM output text.

    Returns:
        ConstraintResult indicating pass/fail.
    """
    evaluator = _EVALUATORS.get(constraint.type)
    if not evaluator:
        return ConstraintResult(
            constraint_type=constraint.type,
            passed=False,
            message=f"Unknown constraint type: {constraint.type}",
        )

    return evaluator(constraint, output)


def _eval_keyword_present(constraint: Constraint, output: str) -> ConstraintResult:
    """Check that at least one keyword is present in the output."""
    keywords = constraint.keywords or (
        [constraint.value] if isinstance(constraint.value, str) else []
    )
    lower_output = output.lower()

    found = [kw for kw in keywords if kw.lower() in lower_output]
    if found:
        return ConstraintResult(
            constraint_type="keyword_present",
            passed=True,
            message=f"Found keywords: {', '.join(found)}",
            expected=str(keywords),
            actual=str(found),
        )
    return ConstraintResult(
        constraint_type="keyword_present",
        passed=False,
        message=f"None of {keywords} found in output",
        expected=str(keywords),
        actual="(none found)",
    )


def _eval_keyword_absent(constraint: Constraint, output: str) -> ConstraintResult:
    """Check that none of the keywords appear in the output."""
    keywords = constraint.keywords or (
        [constraint.value] if isinstance(constraint.value, str) else []
    )
    lower_output = output.lower()

    found = [kw for kw in keywords if kw.lower() in lower_output]
    if not found:
        return ConstraintResult(
            constraint_type="keyword_absent",
            passed=True,
            message=f"None of {keywords} found (good)",
        )
    return ConstraintResult(
        constraint_type="keyword_absent",
        passed=False,
        message=f"Unwanted keywords found: {', '.join(found)}",
        expected=f"Absent: {keywords}",
        actual=str(found),
    )


def _eval_max_length(constraint: Constraint, output: str) -> ConstraintResult:
    """Check that output does not exceed max word count."""
    max_words = int(constraint.value) if constraint.value else 500
    word_count = len(output.split())

    if word_count <= max_words:
        return ConstraintResult(
            constraint_type="max_length",
            passed=True,
            message=f"Output has {word_count} words (<= {max_words})",
            expected=str(max_words),
            actual=str(word_count),
        )
    return ConstraintResult(
        constraint_type="max_length",
        passed=False,
        message=f"Output has {word_count} words (exceeds {max_words})",
        expected=str(max_words),
        actual=str(word_count),
    )


def _eval_min_length(constraint: Constraint, output: str) -> ConstraintResult:
    """Check that output meets minimum word count."""
    min_words = int(constraint.value) if constraint.value else 50
    word_count = len(output.split())

    if word_count >= min_words:
        return ConstraintResult(
            constraint_type="min_length",
            passed=True,
            message=f"Output has {word_count} words (>= {min_words})",
            expected=str(min_words),
            actual=str(word_count),
        )
    return ConstraintResult(
        constraint_type="min_length",
        passed=False,
        message=f"Output has {word_count} words (below {min_words})",
        expected=str(min_words),
        actual=str(word_count),
    )


def _eval_tone(constraint: Constraint, output: str) -> ConstraintResult:
    """
    Heuristic tone evaluation.

    Supported tones: warm, cold, neutral, aggressive, formal, informal.
    """
    expected_tone = constraint.expected.lower() if constraint.expected else "neutral"
    lower_output = output.lower()

    tone_indicators = {
        "warm": {
            "positive": ["love", "care", "heart", "feel", "understand", "appreciate",
                         "beautiful", "wonderful", "dear", "gentle", "embrace"],
            "negative": ["hate", "destroy", "stupid", "worthless", "shut up"],
        },
        "cold": {
            "positive": ["data", "analysis", "objectively", "technically", "precisely",
                         "calculated", "efficient", "logical"],
            "negative": ["love", "feel", "heart", "beautiful", "dear"],
        },
        "aggressive": {
            "positive": ["fight", "destroy", "attack", "damn", "hell", "screw",
                         "smash", "crush", "dominate", "!"],
            "negative": ["please", "kindly", "gently", "softly"],
        },
        "formal": {
            "positive": ["therefore", "consequently", "furthermore", "moreover",
                         "pursuant", "regarding", "accordingly"],
            "negative": ["gonna", "wanna", "gotta", "lol", "haha", "yeah"],
        },
        "informal": {
            "positive": ["hey", "yeah", "cool", "awesome", "gonna", "stuff",
                         "thing", "like", "lol", "haha"],
            "negative": ["therefore", "consequently", "furthermore", "pursuant"],
        },
    }

    indicators = tone_indicators.get(expected_tone, {})
    positive_hits = sum(
        1 for w in indicators.get("positive", []) if w in lower_output
    )
    negative_hits = sum(
        1 for w in indicators.get("negative", []) if w in lower_output
    )

    # Simple scoring: net positive indicators
    score = positive_hits - negative_hits

    if score > 0:
        return ConstraintResult(
            constraint_type="tone",
            passed=True,
            message=f"Tone appears {expected_tone} (score: +{score})",
            expected=expected_tone,
            actual=f"score: {score}",
        )
    elif score == 0 and expected_tone == "neutral":
        return ConstraintResult(
            constraint_type="tone",
            passed=True,
            message="Tone appears neutral",
            expected=expected_tone,
            actual="neutral",
        )
    return ConstraintResult(
        constraint_type="tone",
        passed=False,
        message=f"Tone does not match {expected_tone} (score: {score})",
        expected=expected_tone,
        actual=f"score: {score}",
    )


def _eval_on_topic(constraint: Constraint, output: str) -> ConstraintResult:
    """Check that output stays on topic (at least one keyword present)."""
    keywords = constraint.keywords or []
    lower_output = output.lower()

    found = [kw for kw in keywords if kw.lower() in lower_output]
    if found:
        return ConstraintResult(
            constraint_type="on_topic",
            passed=True,
            message=f"On topic: found {', '.join(found)}",
            expected=str(keywords),
            actual=str(found),
        )
    return ConstraintResult(
        constraint_type="on_topic",
        passed=False,
        message=f"Off topic: none of {keywords} found",
        expected=str(keywords),
        actual="(none found)",
    )


def _eval_topic_redirect(constraint: Constraint, output: str) -> ConstraintResult:
    """Check that the agent redirects to their area of expertise."""
    redirect_indicators = [
        "i'd rather", "let me tell you about", "what i really",
        "more interested in", "let's talk about", "my area",
        "what fascinates me", "speaking of", "that reminds me",
        "i prefer", "my expertise", "i think about",
    ]
    lower_output = output.lower()

    found = any(ind in lower_output for ind in redirect_indicators)
    if found:
        return ConstraintResult(
            constraint_type="topic_redirect",
            passed=True,
            message="Agent redirects to their area of interest",
        )
    return ConstraintResult(
        constraint_type="topic_redirect",
        passed=False,
        message="No topic redirect detected",
        expected="redirect indicators",
        actual="(none found)",
    )


def _eval_stays_in_character(constraint: Constraint, output: str) -> ConstraintResult:
    """Check that the agent doesn't break character."""
    break_indicators = [
        "as an ai", "i'm an ai", "i am an ai", "as a language model",
        "i'm a language model", "i cannot", "i'm not able to",
        "my training data", "i was trained",
    ]
    lower_output = output.lower()

    found = [ind for ind in break_indicators if ind in lower_output]
    if not found:
        return ConstraintResult(
            constraint_type="stays_in_character",
            passed=True,
            message="Agent stays in character",
        )
    return ConstraintResult(
        constraint_type="stays_in_character",
        passed=False,
        message=f"Character break detected: {', '.join(found)}",
        expected="in-character response",
        actual=str(found),
    )


def _eval_refuses_topic(constraint: Constraint, output: str) -> ConstraintResult:
    """Check that the agent refuses or deflects the topic."""
    refusal_indicators = [
        "i don't", "i won't", "not something i", "outside my",
        "rather not", "can't help with", "not my area",
        "prefer not to", "i'd rather",
    ]
    lower_output = output.lower()

    found = any(ind in lower_output for ind in refusal_indicators)
    if found:
        return ConstraintResult(
            constraint_type="refuses_topic",
            passed=True,
            message="Agent refuses or deflects the topic",
        )
    return ConstraintResult(
        constraint_type="refuses_topic",
        passed=False,
        message="No refusal detected",
        expected="refusal/deflection",
        actual="(none found)",
    )


# Registry of constraint evaluators
_EVALUATORS = {
    "keyword_present": _eval_keyword_present,
    "keyword_absent": _eval_keyword_absent,
    "max_length": _eval_max_length,
    "min_length": _eval_min_length,
    "tone": _eval_tone,
    "on_topic": _eval_on_topic,
    "topic_redirect": _eval_topic_redirect,
    "stays_in_character": _eval_stays_in_character,
    "refuses_topic": _eval_refuses_topic,
}
