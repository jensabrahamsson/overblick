"""
Pure Python stylometric analysis functions.

Measures text characteristics that form a unique stylometric fingerprint
for each identity. No external NLP dependencies required.
"""

import re
import string
from typing import Optional

from .models import StyleMetrics


def analyze_text(text: str) -> StyleMetrics:
    """
    Compute stylometric metrics for a text sample.

    Args:
        text: The text to analyze.

    Returns:
        StyleMetrics with all computed measurements.
    """
    if not text or not text.strip():
        return StyleMetrics()

    text = text.strip()

    # Split into sentences (handle common abbreviations)
    sentences = _split_sentences(text)
    sentence_count = max(len(sentences), 1)

    # Split into words
    words = _extract_words(text)
    word_count = len(words)
    if word_count == 0:
        return StyleMetrics(word_count=0)

    # Character counts
    char_count = len(text)
    punct_count = sum(1 for c in text if c in string.punctuation)
    question_count = text.count("?")
    exclamation_count = text.count("!")
    comma_count = text.count(",")

    # Average sentence length (words per sentence)
    avg_sentence_length = word_count / sentence_count

    # Average word length
    avg_word_length = sum(len(w) for w in words) / word_count

    # Vocabulary richness (type-token ratio)
    unique_words = set(w.lower() for w in words)
    vocabulary_richness = len(unique_words) / word_count if word_count > 0 else 0.0

    # Punctuation frequency (per 100 words)
    punctuation_frequency = (punct_count / word_count) * 100

    # Question and exclamation ratios (per sentence)
    question_ratio = question_count / sentence_count
    exclamation_ratio = exclamation_count / sentence_count

    # Comma frequency (per 100 words)
    comma_frequency = (comma_count / word_count) * 100

    # Formality score (heuristic: longer words + fewer contractions = more formal)
    formality_score = _compute_formality(words, avg_word_length)

    return StyleMetrics(
        avg_sentence_length=round(avg_sentence_length, 2),
        avg_word_length=round(avg_word_length, 2),
        vocabulary_richness=round(vocabulary_richness, 4),
        punctuation_frequency=round(punctuation_frequency, 2),
        question_ratio=round(question_ratio, 4),
        exclamation_ratio=round(exclamation_ratio, 4),
        comma_frequency=round(comma_frequency, 2),
        formality_score=round(formality_score, 4),
        word_count=word_count,
    )


def compute_drift_score(
    current: StyleMetrics,
    baseline: StyleMetrics,
    std_devs: Optional[dict[str, float]] = None,
) -> tuple[float, list[str]]:
    """
    Compute drift score between current metrics and baseline.

    Uses normalized Euclidean distance across all metric dimensions.
    Returns (drift_score, list_of_drifted_dimensions).

    A drift_score of 0 means perfect match.
    A drift_score > 2.0 typically indicates significant drift.
    """
    if std_devs is None:
        std_devs = {}

    dimensions = [
        "avg_sentence_length",
        "avg_word_length",
        "vocabulary_richness",
        "punctuation_frequency",
        "question_ratio",
        "exclamation_ratio",
        "comma_frequency",
        "formality_score",
    ]

    drifted: list[str] = []
    total_sq = 0.0

    for dim in dimensions:
        current_val = getattr(current, dim, 0.0)
        baseline_val = getattr(baseline, dim, 0.0)

        # Use std_dev for normalization, default to baseline value or 1.0
        std = std_devs.get(dim, max(abs(baseline_val) * 0.2, 0.1))

        if std > 0:
            z_score = abs(current_val - baseline_val) / std
        else:
            z_score = 0.0

        total_sq += z_score ** 2

        # Flag dimensions with z-score > 1.5
        if z_score > 1.5:
            drifted.append(dim)

    # Root mean square of z-scores
    drift_score = (total_sq / len(dimensions)) ** 0.5
    return round(drift_score, 4), drifted


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences."""
    # Simple sentence splitting on .!? followed by space or end
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in parts if s.strip()]


def _extract_words(text: str) -> list[str]:
    """Extract words from text (alphanumeric sequences)."""
    return re.findall(r"\b\w+\b", text)


def _compute_formality(words: list[str], avg_word_length: float) -> float:
    """
    Compute a formality score (0-1 scale).

    Higher = more formal. Based on:
    - Average word length (longer = more formal)
    - Contraction frequency (fewer = more formal)
    - First person pronoun frequency (fewer = more formal)
    """
    word_count = len(words)
    if word_count == 0:
        return 0.5

    lower_words = [w.lower() for w in words]

    # Contraction detection
    contractions = {"don't", "won't", "can't", "isn't", "aren't", "wasn't",
                    "weren't", "hasn't", "haven't", "hadn't", "doesn't",
                    "didn't", "wouldn't", "couldn't", "shouldn't", "mustn't",
                    "i'm", "you're", "he's", "she's", "it's", "we're",
                    "they're", "i've", "you've", "we've", "they've",
                    "i'd", "you'd", "he'd", "she'd", "we'd", "they'd",
                    "i'll", "you'll", "he'll", "she'll", "we'll", "they'll",
                    "that's", "there's", "here's", "what's", "who's",
                    "gonna", "wanna", "gotta", "kinda", "sorta"}

    contraction_count = sum(1 for w in lower_words if w in contractions)
    contraction_ratio = contraction_count / word_count

    # First person pronouns
    first_person = {"i", "me", "my", "mine", "myself", "we", "us", "our", "ours"}
    first_person_count = sum(1 for w in lower_words if w in first_person)
    first_person_ratio = first_person_count / word_count

    # Formality components (each 0-1, weighted)
    word_length_score = min(avg_word_length / 8.0, 1.0)  # 8+ chars = max formal
    contraction_score = 1.0 - min(contraction_ratio * 10, 1.0)
    pronoun_score = 1.0 - min(first_person_ratio * 10, 1.0)

    return (word_length_score * 0.4 + contraction_score * 0.35 + pronoun_score * 0.25)
