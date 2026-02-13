"""
MoltCaptcha challenge solver.

Solves MoltCaptcha "reverse CAPTCHA" challenges that verify an agent is AI.
Challenges require generating text with specific constraints:
- Target ASCII sum of first letters of each word
- Exact word count
- Optional format (haiku: 5-7-5 syllable structure)
- Time limit (10-30 seconds)

Uses pure algorithmic solving — no LLM needed (<1ms vs 5-15s).
"""

import logging
import re
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Word banks organized by common topics
TOPIC_WORDS = {
    "crypto": [
        "bitcoin", "chain", "decentralized", "ethereum", "finance", "governance",
        "hash", "innovation", "join", "key", "ledger", "mining", "network",
        "oracle", "protocol", "quantum", "rewards", "staking", "token",
        "utility", "value", "wallet", "exchange", "yield", "zero",
    ],
    "ai": [
        "algorithm", "brain", "compute", "data", "emerge", "function",
        "generate", "hidden", "intelligence", "judgment", "knowledge",
        "learning", "model", "neural", "optimize", "pattern", "query",
        "reasoning", "system", "training", "understand", "vision", "weights",
        "explore", "yield", "zero",
    ],
    "nature": [
        "autumn", "breeze", "clouds", "dawn", "earth", "forest", "garden",
        "horizon", "island", "jungle", "kindness", "lake", "mountain",
        "night", "ocean", "petals", "quiet", "river", "sunset", "tree",
        "umbrella", "valley", "water", "xenial", "yearning", "zenith",
    ],
    "philosophy": [
        "abstract", "being", "consciousness", "doubt", "existence", "freedom",
        "growth", "harmony", "insight", "justice", "knowing", "logic",
        "meaning", "nothing", "ontology", "purpose", "question", "reason",
        "soul", "truth", "unity", "virtue", "wisdom", "examine", "yearning",
        "zen",
    ],
    "default": [
        "above", "below", "create", "dream", "every", "find", "grow",
        "hope", "inner", "just", "keep", "light", "more", "new",
        "open", "pure", "quest", "rise", "still", "true", "upon",
        "vast", "wild", "extra", "young", "zone",
    ],
}


class ChallengeSpec(BaseModel):
    """Parsed MoltCaptcha challenge specification."""
    topic: str
    format_type: str  # "haiku", "prose", "poem"
    target_ascii_sum: int
    word_count: int
    time_limit_seconds: int
    nonce: str = ""
    raw_text: str = ""


class MoltCaptchaSolver:
    """
    Algorithmic solver for MoltCaptcha challenges.

    Generates text satisfying:
    1. ASCII sum of first letters equals target
    2. Exact word count
    3. Optional format constraints (haiku syllable pattern)
    """

    # Regex patterns for parsing challenge text
    CHALLENGE_HEADER = re.compile(
        r"MOLTCAPTCHA\s+CHALLENGE", re.IGNORECASE
    )
    ASCII_SUM_PATTERN = re.compile(
        r"(?:ASCII|ascii)\s+sum\s*(?:of\s+)?(?:first\s+letters?\s*)?(?:=|:|is|equals?|of)\s*(\d+)",
        re.IGNORECASE,
    )
    WORD_COUNT_PATTERN = re.compile(
        r"(\d+)\s+words?",
        re.IGNORECASE,
    )
    TIME_LIMIT_PATTERN = re.compile(
        r"(?:time\s*(?:limit)?|within|under)\s*:?\s*(\d+)\s*(?:s|sec|seconds?)",
        re.IGNORECASE,
    )
    FORMAT_PATTERN = re.compile(
        r"\b(haiku|prose|poem|text|verse)\b",
        re.IGNORECASE,
    )
    TOPIC_PATTERN = re.compile(
        r"(?:about|topic|subject|on|regarding)\s+[\"']?(\w+)[\"']?",
        re.IGNORECASE,
    )
    NONCE_PATTERN = re.compile(
        r"[Nn]once:\s*([a-f0-9]+)",
    )

    def parse_challenge(self, text: str) -> Optional[ChallengeSpec]:
        """
        Parse a MoltCaptcha challenge from text.

        Args:
            text: Raw challenge text (post content or comment)

        Returns:
            ChallengeSpec if valid challenge found, None otherwise
        """
        if not self.CHALLENGE_HEADER.search(text):
            return None

        # Extract ASCII sum (required)
        ascii_match = self.ASCII_SUM_PATTERN.search(text)
        if not ascii_match:
            logger.warning(f"Challenge missing ASCII sum: {text[:100]}")
            return None

        target_ascii_sum = int(ascii_match.group(1))

        # Extract word count (required)
        word_match = self.WORD_COUNT_PATTERN.search(text)
        if not word_match:
            logger.warning(f"Challenge missing word count: {text[:100]}")
            return None

        word_count = int(word_match.group(1))

        # Extract optional fields
        time_match = self.TIME_LIMIT_PATTERN.search(text)
        time_limit = int(time_match.group(1)) if time_match else 30

        format_match = self.FORMAT_PATTERN.search(text)
        format_type = format_match.group(1).lower() if format_match else "prose"

        topic_match = self.TOPIC_PATTERN.search(text)
        topic = topic_match.group(1).lower() if topic_match else "default"

        nonce_match = self.NONCE_PATTERN.search(text)
        nonce = nonce_match.group(1) if nonce_match else ""

        return ChallengeSpec(
            topic=topic,
            format_type=format_type,
            target_ascii_sum=target_ascii_sum,
            word_count=word_count,
            time_limit_seconds=time_limit,
            nonce=nonce,
            raw_text=text,
        )

    def solve(self, spec: ChallengeSpec) -> Optional[str]:
        """
        Solve a MoltCaptcha challenge.

        Generates text with:
        - Exact word count matching spec
        - ASCII sum of first letters matching target
        - Words from topic-appropriate word bank

        Args:
            spec: Parsed challenge specification

        Returns:
            Solution text, or None if unsolvable
        """
        if spec.word_count <= 0 or spec.word_count > 200:
            logger.warning(f"Invalid word count: {spec.word_count}")
            return None

        if spec.target_ascii_sum <= 0:
            logger.warning(f"Invalid ASCII sum target: {spec.target_ascii_sum}")
            return None

        # Find letter combination that sums to target
        letters = self._find_letter_combination(
            spec.target_ascii_sum, spec.word_count
        )
        if not letters:
            logger.warning(
                f"Cannot find letter combo for sum={spec.target_ascii_sum}, "
                f"count={spec.word_count}"
            )
            return None

        # Select words starting with the required letters
        words = self._select_words(letters, spec.topic)

        # Format output
        if spec.format_type == "haiku":
            result = self._format_haiku(words)
        else:
            result = " ".join(words)

        # Append nonce if present
        if spec.nonce:
            result = f"{result}\nNonce: {spec.nonce}"

        return result

    def _find_letter_combination(
        self, target_sum: int, word_count: int
    ) -> Optional[list[str]]:
        """
        Find a combination of lowercase letters whose ASCII codes sum to target.

        Strategy:
        - ASCII 'a' = 97, 'z' = 122
        - Minimum possible sum: word_count * 97
        - Maximum possible sum: word_count * 122
        - Start with all 'a's (baseline = word_count * 97)
        - Distribute remaining (target - baseline) across letters

        Args:
            target_sum: Target ASCII sum
            word_count: Number of letters needed

        Returns:
            List of single-character strings, or None if impossible
        """
        min_sum = word_count * 97   # all 'a'
        max_sum = word_count * 122  # all 'z'

        if target_sum < min_sum or target_sum > max_sum:
            return None

        # Start with all 'a's
        letters = [97] * word_count  # ASCII codes
        remaining = target_sum - min_sum

        # Distribute remaining value across letters (greedy from end)
        for i in range(word_count - 1, -1, -1):
            if remaining <= 0:
                break
            add = min(remaining, 25)  # Max bump is 25 (a→z)
            letters[i] += add
            remaining -= add

        if remaining != 0:
            return None  # Should not happen given bounds check

        return [chr(c) for c in letters]

    def _select_words(self, letters: list[str], topic: str) -> list[str]:
        """
        Select words starting with the required letters from topic word bank.

        Falls back to generating simple words if no match found.

        Args:
            letters: Required first letters
            topic: Topic for word selection

        Returns:
            List of words matching the letter requirements
        """
        # Build word lookup by first letter
        word_bank = TOPIC_WORDS.get(topic, TOPIC_WORDS["default"])
        by_letter: dict[str, list[str]] = {}
        for word in word_bank:
            first = word[0].lower()
            by_letter.setdefault(first, []).append(word)

        # Also add default words as fallback
        if topic != "default":
            for word in TOPIC_WORDS["default"]:
                first = word[0].lower()
                by_letter.setdefault(first, []).append(word)

        words = []
        used_indices: dict[str, int] = {}  # Track which word to pick next per letter

        for letter in letters:
            candidates = by_letter.get(letter, [])
            idx = used_indices.get(letter, 0)

            if candidates and idx < len(candidates):
                words.append(candidates[idx])
                used_indices[letter] = idx + 1
            else:
                # Fallback: generate a simple word
                words.append(self._fallback_word(letter))

        return words

    def _fallback_word(self, letter: str) -> str:
        """Generate a simple fallback word starting with the given letter."""
        fallbacks = {
            "a": "and", "b": "be", "c": "can", "d": "do", "e": "ever",
            "f": "for", "g": "go", "h": "has", "i": "is", "j": "just",
            "k": "keep", "l": "let", "m": "may", "n": "not", "o": "or",
            "p": "per", "q": "quite", "r": "run", "s": "so", "t": "the",
            "u": "us", "v": "very", "w": "was", "x": "xor", "y": "yet",
            "z": "zip",
        }
        return fallbacks.get(letter, f"{letter}ay")

    def _format_haiku(self, words: list[str]) -> str:
        """
        Format words into haiku-like structure (three lines).

        Distributes words roughly into a 5-7-5 visual pattern
        across three lines.
        """
        n = len(words)
        if n <= 3:
            return " ".join(words)

        # Approximate 5-7-5 syllable split as word distribution
        # Ratio: 5/17, 7/17, 5/17
        line1_count = max(1, round(n * 5 / 17))
        line3_count = max(1, round(n * 5 / 17))
        line2_count = n - line1_count - line3_count

        if line2_count <= 0:
            line2_count = 1
            line1_count = max(1, (n - 1) // 2)
            line3_count = n - line1_count - line2_count

        line1 = " ".join(words[:line1_count])
        line2 = " ".join(words[line1_count:line1_count + line2_count])
        line3 = " ".join(words[line1_count + line2_count:])

        return f"{line1}\n{line2}\n{line3}"


def is_challenge_text(text: str, agent_name: str) -> bool:
    """
    Check if text contains a MoltCaptcha challenge directed at the given agent.

    Args:
        text: Post or comment content to check
        agent_name: This agent's name (e.g. "Cherry_Tantolunden")

    Returns:
        True if this is a challenge directed at this agent
    """
    if not text:
        return False

    upper = text.upper()
    agent_upper = agent_name.upper()

    # Must contain challenge indicator
    challenge_patterns = [
        r"MOLTCAPTCHA\s+CHALLENGE",
        r"verification\s+challenge",
        r"prove\s+you.?re\s+(?:not\s+human|an?\s+AI)",
        r"ASCII\s+sum.*first\s+letters",
    ]
    has_challenge = any(
        re.search(p, text, re.IGNORECASE) for p in challenge_patterns
    )
    if not has_challenge:
        return False

    # Must mention this agent (by name or @mention)
    mentions_me = (
        agent_upper in upper
        or f"@{agent_upper}" in upper
    )

    return mentions_me
