"""
Moltbook challenge deobfuscation engine.

Removes case-mixing, letter-doubling, and space injection from
challenge text used by the Moltbook API verification system.

Community findings (issue #134):
- Obfuscation: case-mixing (tWeNtY) + letter-doubling (tWwEeNnTtYy) + space injection (f i v e)
"""

import re
from typing import Optional


# ── Deobfuscation utilities ──────────────────────────────────────────────────
# Moltbook challenges use three obfuscation techniques (per community reports):
#   1. Case-mixing: random upper/lower casing  →  tWeNtY → twenty
#   2. Letter-doubling: each char repeated with case swap → tWwEeNnTtYy → twenty
#   3. Space injection: words split with spaces/noise → "f i v e" → "five"
# We strip doubling first, then normalize case, then reassemble fragments.


def _strip_letter_doubling(word: str) -> str:
    """Remove letter-doubling obfuscation from a single word.

    Greedy scan stripping opposite-case pairs (Ee, Ll, Ss) which are
    obfuscation doubles. Same-case pairs (ll, ee) are preserved as
    natural English doubles. After stripping a pair, consumes one
    trailing same-letter residual (handles odd runs like SsS → S).

    Examples:
        tWwEeNnTtYy → twenty
        tThHrReEeE  → threE → three (natural double-e preserved)
        hello       → hello (natural ll preserved)
        lOoBbSsStTeR → lOBSteR → lobster

    Short words (<4 chars) are returned as-is.
    """
    if len(word) < 4:
        return word

    result = []
    i = 0
    while i < len(word):
        result.append(word[i])
        if (
            i + 1 < len(word)
            and word[i].isalpha()
            and word[i + 1].isalpha()
            and word[i].lower() == word[i + 1].lower()
            and word[i] != word[i + 1]  # Must be different case
        ):
            i += 2  # Skip the doubled char
            # Consume one trailing same-letter residual from odd runs (SsS → S)
            if (
                i < len(word)
                and word[i].isalpha()
                and word[i].lower() == word[i - 2].lower()
            ):
                i += 1
        else:
            i += 1

    cleaned = "".join(result)
    if len(cleaned) < len(word):
        return cleaned
    return word


# ── Fragment reassembly for space-injection obfuscation ─────────────────────
# Moltbook splits words with spaces and noise chars: "f i v e" instead of "five".
# After per-token deobfuscation, we greedily merge short fragments back into
# known words (number words + challenge domain vocabulary).

# Word-form number dictionaries (shared with arithmetic_solver)
_ONES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_NUMBER_WORDS = frozenset(_ONES.keys()) | frozenset(_TENS.keys()) | frozenset(
    {"hundred", "thousand"}
)

_CHALLENGE_VOCAB = frozenset(
    {
        "newtons", "newton", "lobster", "lobsters",
        "meters", "meter", "velocity", "speed",
        "force", "claw", "claws", "dominance",
        "fight", "combined", "total", "gains",
        "loses", "slows", "drops", "antenna", "antennas",
        "plus", "minus", "more", "less",
        "exerts", "swims", "swimming", "molting", "water",
    }
)

_REASSEMBLY_TARGETS = _NUMBER_WORDS | _CHALLENGE_VOCAB


def _reassemble_fragments(tokens: list[str]) -> list[str]:
    """Reassemble space-injected word fragments into known words.

    Scans deobfuscated tokens and greedily merges short fragments that
    together form a word in _REASSEMBLY_TARGETS (number words + challenge
    domain vocabulary). Longest match wins to minimize false merges.

    Preserves trailing punctuation from the last merged token.
    Single tokens that are already valid words are never merged further.
    """
    MAX_MERGE = 5
    result = []
    i = 0
    while i < len(tokens):
        best_match = None
        best_length = 0

        for k in range(min(MAX_MERGE, len(tokens) - i), 1, -1):
            merged = "".join(
                "".join(c for c in tokens[j] if c.isalpha())
                for j in range(i, i + k)
            ).lower()
            if merged in _REASSEMBLY_TARGETS:
                best_match = merged
                best_length = k
                break

        if best_match:
            last = tokens[i + best_length - 1]
            trailing = ""
            if last and last[-1] in ",.?!;:":
                trailing = last[-1]
            result.append(best_match + trailing)
            i += best_length
        else:
            result.append(tokens[i])
            i += 1

    return result


# Explicit correction map for common deobfuscation artifacts that are beyond
# edit-distance-1 or too short for fuzzy matching.  Keyed by lowercase token.
_DEOBFUSCATION_FIXES: dict[str, str] = {
    # Letter-doubling strips natural doubles: "newtons" → "notons" (ee→o, w dropped)
    "notons": "newtons",
    "noton": "newton",
    "nootons": "newtons",
    "nooton": "newton",
    "neutons": "newtons",
    "neuton": "newton",
    # Short words where edit-distance-1 threshold is too high
    "ads": "adds",
    "ad": "adds",
    "gans": "gains",
    # Truncated words from deobfuscation
    "thre": "three",
    "for": "four",  # unconditional — all challenges are numeric contexts
    "fiv": "five",
    "seve": "seven",
    "eigh": "eight",
    "nin": "nine",
    "velocitee": "velocity",
    "velawtee": "velocity",
    "velawcitee": "velocity",
}


def _edit_distance_one(a: str, b: str) -> bool:
    """Check if two strings are exactly edit-distance 1 apart.

    Handles substitution (same length, 1 char differs) and
    insertion/deletion (length differs by 1).
    """
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    skips = 0
    j = 0
    for i in range(len(long_)):
        if j < len(short) and long_[i] == short[j]:
            j += 1
        else:
            skips += 1
    return skips == 1 and j == len(short)


def _correct_known_words(tokens: list[str]) -> list[str]:
    """Correct single-token deobfuscation artifacts against known vocabulary.

    Two strategies:
    1. Explicit correction map for known artifacts (any length)
    2. Edit-distance-1 fuzzy match against vocabulary for words >= 4 chars

    Letter-doubling stripping can eat natural doubles (e.g. the 'ee' in
    'fourteen' → 'fourten'). This pass catches both predictable artifacts
    (via the map) and novel ones (via edit-distance).
    """
    result = []
    for token in tokens:
        alpha = "".join(c for c in token if c.isalpha())
        trailing = token[len(alpha):] if alpha else ""
        low = alpha.lower()

        # Strategy 1: explicit correction map
        if low in _DEOBFUSCATION_FIXES:
            result.append(_DEOBFUSCATION_FIXES[low] + trailing)
            continue

        # Strategy 2: edit-distance-1 fuzzy match (>= 4 chars)
        if len(low) >= 4 and low not in _REASSEMBLY_TARGETS:
            for target in _REASSEMBLY_TARGETS:
                if len(target) >= 4 and abs(len(low) - len(target)) <= 1:
                    if _edit_distance_one(low, target):
                        result.append(target + trailing)
                        break
            else:
                result.append(token)
        else:
            result.append(token)
    return result


def deobfuscate_challenge(text: str) -> str:
    """Remove case-mixing, letter-doubling, and space injection from challenge text.

    Processes each word independently:
    1. Extract only alpha characters (strips obfuscation punctuation like . ^ / ~)
    2. Strip letter-doubling (lOoBbSsStTeR → loBster)
    3. Normalize to lowercase (loBster → lobster)
    4. Reassemble space-injected fragments (f i v e → five)
    5. Correct near-miss artifacts against known vocabulary (fourten → fourteen)

    Non-alphabetic tokens (numbers, punctuation, operators) are preserved as-is.
    Trailing punctuation (? ! , .) on tokens is preserved.
    """
    tokens = text.split()
    result = []
    for token in tokens:
        # Preserve non-alpha tokens (numbers, operators, punctuation)
        if not any(c.isalpha() for c in token):
            result.append(token)
            continue

        # Extract ONLY alpha characters — discard obfuscation noise
        # (dots, carets, slashes, tildes, brackets injected between letters)
        alpha_only = "".join(c for c in token if c.isalpha())

        # Preserve only real trailing punctuation from the original token
        trailing = ""
        if token and token[-1] in ",.?!;:":
            trailing = token[-1]

        if alpha_only:
            cleaned = _strip_letter_doubling(alpha_only)
            result.append(f"{cleaned.lower()}{trailing}")
        else:
            result.append(token)

    # Reassemble space-injected fragments (e.g. "for ty" → "forty")
    result = _reassemble_fragments(result)
    # Correct single-token artifacts (e.g. "fourten" → "fourteen")
    result = _correct_known_words(result)
    return " ".join(result)
