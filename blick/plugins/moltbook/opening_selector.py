"""
Opening phrase selector — variety in engagement openings.

Prevents repetitive response openings by tracking recently used
phrases and ensuring variety across interactions.
"""

import logging
import random
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# Default opening phrases (identity can override via YAML)
DEFAULT_OPENINGS = [
    "",  # No opening (just dive in)
    "Interesting point.",
    "This caught my attention.",
    "I've been thinking about this.",
    "Worth considering:",
    "Here's the thing:",
    "Let me push back on this slightly.",
    "I see what you're getting at.",
]


class OpeningSelector:
    """
    Selects varied opening phrases for responses.

    Tracks recently used openings to avoid repetition.
    """

    def __init__(
        self,
        phrases: Optional[list[str]] = None,
        history_size: int = 10,
    ):
        self._phrases = phrases or DEFAULT_OPENINGS
        self._recent: deque[str] = deque(maxlen=history_size)

    def select(self) -> str:
        """Select a varied opening phrase."""
        # Filter out recently used phrases (if we have enough variety)
        available = [p for p in self._phrases if p not in self._recent]
        if not available:
            available = self._phrases

        choice = random.choice(available)
        if choice:  # Don't track empty string
            self._recent.append(choice)

        return choice

    def add_phrases(self, phrases: list[str]) -> None:
        """Add additional phrases to the pool."""
        self._phrases.extend(phrases)
