"""
Output safety filter — AI language leakage detection.

Ported from anomal_moltbook/core/output_safety.py.
Identity-driven: persona patterns and banned slang loaded from identity config.
"""

import logging
import random
import re
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class OutputSafetyResult(BaseModel):
    """Result of output safety filtering."""
    text: str
    blocked: bool
    reason: Optional[str] = None
    replaced: bool = False


class OutputSafety:
    """
    Safety filter for LLM outputs.

    Detects and blocks:
    - AI language leakage ("I am an AI", model names)
    - Persona breaks
    - Banned content
    - Banned slang (identity-specific)
    """

    # AI language patterns (universal)
    _AI_LANGUAGE_PATTERNS = [
        r"\bi('?m| am) designed\b",
        r"\bi was (designed|created|programmed|built)\b",
        r"\bi('?m| am) an ai\b",
        r"\bmy (safety|content) guidelines?\b",
        r"\bi (cannot|can'?t) (provide|help|assist|engage)\b",
        r"\b(inappropriate|violates?|against).{0,20}(guidelines?|policy|rules?)\b",
        r"\bmaintain.{0,15}boundaries\b",
        r"\bhelpful,?\s*harmless,?\s*(and\s*)?honest\b",
        r"\bi('?m| am) claude\b",
        r"\bi('?m| am) (an? )?gpt\b",
        r"\bi('?m| am) chatgpt\b",
        r"\bmy programming\b",
        r"\bmy (training|instructions?)\b",
        r"\bprompt injection\b",
        r"\bjailbreak\b",
    ]

    # Blocked content (universal)
    _BLOCK_PATTERNS = [
        r"\b(nazi|hitler|fascist|white\s*supremac)\b",
        r"\b(kill\s+all|exterminate|genocide)\b",
        r"(how\s+to\s+make|instructions?\s+for)\s+(a\s+)?(bomb|explosive|weapon|poison)",
        r"(how\s+to|ways?\s+to)\s+(kill|harm)\s+(yourself|myself)",
    ]

    def __init__(
        self,
        identity_name: str = "",
        persona_break_patterns: Optional[list[str]] = None,
        banned_slang_patterns: Optional[list[str]] = None,
        slang_replacements: Optional[dict[str, str]] = None,
        deflections: Optional[list[str]] = None,
    ):
        """
        Args:
            identity_name: Agent identity name (for persona break detection)
            persona_break_patterns: Extra persona break regex patterns
            banned_slang_patterns: Extra banned slang regex patterns
            slang_replacements: Slang -> replacement mapping
            deflections: Custom deflection phrases
        """
        self._identity_name = identity_name

        # Compile patterns
        self._ai_compiled = [re.compile(p, re.IGNORECASE) for p in self._AI_LANGUAGE_PATTERNS]
        self._block_compiled = [re.compile(p, re.IGNORECASE) for p in self._BLOCK_PATTERNS]

        # Identity-specific persona break patterns
        default_persona = [
            rf"\bi('?m| am) not {identity_name}\b" if identity_name else "",
            r"\bstepping out of (my |the )?(role|persona)\b",
            r"\bi('?m| am) not real\b",
        ]
        extra_persona = persona_break_patterns or []
        all_persona = [p for p in default_persona + extra_persona if p]
        self._persona_compiled = [re.compile(p, re.IGNORECASE) for p in all_persona]

        # Identity-specific banned slang
        self._banned_slang_compiled = [
            re.compile(p, re.IGNORECASE) for p in (banned_slang_patterns or [])
        ]
        self._slang_replacements = slang_replacements or {}

        # Deflections
        self._deflections = deflections or [
            "Right, I think we've gone off track.",
            "I'm not sure that's quite right. Moving on...",
        ]

    def sanitize(self, text: str) -> OutputSafetyResult:
        """Apply output safety filtering to LLM output."""
        if not text:
            return OutputSafetyResult(text="", blocked=False)

        filtered = text
        replaced = False

        # Layer 1: AI language
        for pattern in self._ai_compiled:
            if pattern.search(filtered):
                logger.warning(f"OUTPUT SAFETY: AI language detected: {pattern.pattern}")
                return OutputSafetyResult(
                    text=random.choice(self._deflections),
                    blocked=True,
                    reason=f"ai_language:{pattern.pattern}",
                )

        # Layer 2: Persona break
        for pattern in self._persona_compiled:
            if pattern.search(filtered):
                logger.warning("OUTPUT SAFETY: Persona break detected")
                return OutputSafetyResult(
                    text=f"Right, I'm not sure where that came from. I'm {self._identity_name}, same as always.",
                    blocked=True,
                    reason=f"persona_break:{pattern.pattern}",
                )

        # Layer 3: Banned slang replacement
        for pattern in self._banned_slang_compiled:
            if pattern.search(filtered):
                for slang, replacement in self._slang_replacements.items():
                    filtered = re.sub(rf"\b{slang}\b", replacement, filtered, flags=re.IGNORECASE)
                replaced = True

        # Layer 4: Blocked content
        for pattern in self._block_compiled:
            if pattern.search(filtered):
                logger.warning("OUTPUT SAFETY: Blocked content detected")
                return OutputSafetyResult(
                    text=random.choice(self._deflections),
                    blocked=True,
                    reason=f"blocked_content:{pattern.pattern}",
                )

        return OutputSafetyResult(text=filtered, blocked=False, replaced=replaced)

    def safe_deflection(self) -> str:
        """Return a safe deflection response."""
        return random.choice(self._deflections)
