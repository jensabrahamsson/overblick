"""
LearningExtractor — extract learning candidates from text.

Moved from SafeLearningModule/LearningCapability. Pattern-based
extraction looking for teaching indicators in conversation text.
"""

import logging

logger = logging.getLogger(__name__)

# Teaching indicators that signal potential learnable content
TEACHING_INDICATORS = [
    "did you know",
    "actually",
    "fun fact",
    "research shows",
    "studies show",
    "according to",
    "it turns out",
]

# Maximum candidates to extract from a single text
MAX_CANDIDATES = 3

# Minimum sentence length to consider
MIN_SENTENCE_LENGTH = 30


class LearningExtractor:
    """Extracts potential learning candidates from text."""

    @staticmethod
    def extract(
        text: str,
        source_agent: str = "",
    ) -> list[dict]:
        """
        Extract potential learnings from conversation text.

        Looks for teaching indicators (e.g. "did you know", "research shows")
        and extracts the containing sentences as candidates.

        Args:
            text: Text to scan for learnings
            source_agent: Name of the agent who produced the text

        Returns:
            List of candidate dicts with keys: content, category, context, agent
        """
        if not text:
            return []

        candidates = []
        text_lower = text.lower()

        for indicator in TEACHING_INDICATORS:
            if indicator in text_lower:
                sentences = [s.strip() for s in text.split(". ") if s.strip()]
                for sentence in sentences:
                    if (
                        indicator in sentence.lower()
                        and len(sentence) >= MIN_SENTENCE_LENGTH
                    ):
                        candidates.append({
                            "content": sentence[:200].strip(),
                            "category": "factual",
                            "context": text[:200],
                            "agent": source_agent,
                        })
                        break  # One candidate per indicator

        return candidates[:MAX_CANDIDATES]
