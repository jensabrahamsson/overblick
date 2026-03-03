"""
Programmatic arithmetic solver for Moltbook challenges.

Fast-path solver that runs before any LLM call.
Based on community implementation (moltbook-mcp by p4stoboy).
Three strategies: digit expressions, operator split, word numbers.

All arithmetic is done via safe manual parsing — no dynamic code execution.
"""

import re
from typing import Optional

from .deobfuscator import _ONES, _TENS, _edit_distance_one

# Words to ignore when parsing word numbers
_FILLER = frozenset(
    {
        "what",
        "is",
        "whats",
        "the",
        "of",
        "a",
        "an",
        "and",
        "are",
        "how",
        "many",
        "much",
        "if",
        "you",
        "have",
        "had",
        "get",
        "got",
        "give",
        "gave",
        "away",
        "left",
        "remain",
        "remains",
        "remaining",
        "result",
        "answer",
        "calculate",
        "solve",
        "compute",
        "find",
        "equal",
        "equals",
        "to",
        "from",
        "with",
        "do",
        "does",
        "would",
        "will",
        "be",
        "by",
        "apples",
        "oranges",
        "items",
        "things",
        "objects",
        "numbers",
    }
)

_DIGIT_EXPR_RE = re.compile(r"[\d+\-*/().^ ]+")
_OP_DETECT = {
    "mul": re.compile(r"\b(times|multipl\w*|product|multiplied)\b"),
    "div": re.compile(r"\b(divid\w*|ratio|split)\b"),
    "sub": re.compile(
        r"\b(subtract\w*|minus|less|reduce\w*|remain\w*|take\s+away|gave?\s+away|slow\w*|los[ei]\w*|drop\w*|decreas\w*|fell|lower\w*|dip\w*)\b"
    ),
}


def _fuzzy_match(word: str, dictionary: dict[str, int]) -> tuple[str, int] | None:
    """Match a word against a dictionary with tolerance for deobfuscation artifacts.

    Conservative matching to avoid false positives from obfuscation fragments:
    - Exact match always accepted
    - Prefix match only if word covers >= 80% of the key (e.g. 'thre'→'three')
    - Edit-distance-1 for words >= 6 chars (catches 'fourten'→'fourteen')
    - Subsequence matching disabled (too many false positives with short fragments)
    """
    if word in dictionary:
        return (word, dictionary[word])

    # Strict prefix match — word must cover most of the key to avoid
    # false positives like "thir" → "thirteen" (4/8 = 50%, rejected)
    for key, val in dictionary.items():
        if len(word) >= 4 and key.startswith(word) and len(word) >= len(key) * 0.8:
            return (key, val)
        if len(key) >= 4 and word.startswith(key) and len(key) >= len(word) * 0.8:
            return (key, val)

    # Edit-distance-1 for long words (deobfuscation artifacts like "fourten")
    if len(word) >= 6:
        for key, val in dictionary.items():
            if len(key) >= 6 and abs(len(word) - len(key)) <= 1:
                if _edit_distance_one(word, key):
                    return (key, val)

    return None


def _is_subsequence(needle: str, haystack: str) -> bool:
    """Check if needle is a subsequence of haystack."""
    it = iter(haystack)
    return all(c in it for c in needle)


def _extract_word_numbers(text: str) -> list[int]:
    """Extract numbers from word-form text.

    Handles: 'twenty three' → [23], 'five plus thirteen' → [5, 13].
    Tokens not recognized as numbers or filler words are skipped.
    """
    # Strip trailing punctuation before filtering — "four," → "four"
    raw_tokens = text.lower().split()
    tokens = []
    for t in raw_tokens:
        stripped = t.rstrip(",.?!;:")
        if stripped and stripped.isalpha() and stripped not in _FILLER:
            tokens.append(stripped)
    numbers = []
    i = 0

    while i < len(tokens):
        # Try tens + ones combo (e.g. "twenty three" → 23)
        # Skip fuzzy _TENS if the token is an exact _ONES word
        # (prevents "eight" from fuzzy-matching to "eighty" → 80)
        if i + 1 < len(tokens) and tokens[i] not in _ONES:
            tens_match = _fuzzy_match(tokens[i], _TENS)
            if tens_match:
                ones_match = _fuzzy_match(tokens[i + 1], _ONES)
                if ones_match and 1 <= ones_match[1] <= 9:
                    numbers.append(tens_match[1] + ones_match[1])
                    i += 2
                    continue
                # Tens without ones
                numbers.append(tens_match[1])
                i += 1
                continue

        # Try single token — prefer exact _ONES match over fuzzy _TENS
        # (prevents "eight" from fuzzy-matching to "eighty" → 80)
        ones_match = _fuzzy_match(tokens[i], _ONES)
        if ones_match and ones_match[0] == tokens[i]:
            # Exact ones match — use it directly
            numbers.append(ones_match[1])
            i += 1
            continue

        tens_match = _fuzzy_match(tokens[i], _TENS)
        if tens_match:
            numbers.append(tens_match[1])
            i += 1
            continue

        if ones_match:
            numbers.append(ones_match[1])
            i += 1
            continue

        i += 1

    return numbers


def _detect_operation(text: str) -> str:
    """Detect arithmetic operation from text. Defaults to 'add'."""
    lower = text.lower()
    for op, pattern in _OP_DETECT.items():
        if pattern.search(lower):
            return op
    if "plus" in lower or "add" in lower or "sum" in lower:
        return "add"
    return "add"


def _compute(numbers: list[int | float], op: str) -> float | None:
    """Compute result from numbers and operation."""
    if len(numbers) < 2:
        return None
    if op == "add":
        return sum(numbers)
    if op == "sub":
        result = numbers[0]
        for n in numbers[1:]:
            result -= n
        return result
    if op == "mul":
        result = numbers[0]
        for n in numbers[1:]:
            result *= n
        return result
    if op == "div":
        if numbers[1] == 0:
            return None
        return float(numbers[0]) / float(numbers[1])
    return None


def _solve_digit_expression(text: str) -> str | None:
    """Solve pure digit arithmetic expressions like '32 + 18' or '5 * 3'.

    Uses safe manual parsing — no dynamic code execution.
    Supports +, -, *, /, ^.
    """
    matches = _DIGIT_EXPR_RE.findall(text)
    if not matches:
        return None

    # Pick the longest candidate containing an operator
    candidates = [m.strip() for m in matches if re.search(r"[+\-*/^]", m) and re.search(r"\d", m)]
    if not candidates:
        return None

    expr = max(candidates, key=len)
    if len(expr) > 200:
        return None

    expr = expr.replace("^", "**")

    # Try simple binary: a op b
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*([+\-*/]|\*\*)\s*(-?\d+(?:\.\d+)?)\s*$", expr)
    if m:
        a, op, b = float(m.group(1)), m.group(2), float(m.group(3))
        try:
            if op == "+":
                return f"{a + b:.2f}"
            if op == "-":
                return f"{a - b:.2f}"
            if op == "*":
                return f"{a * b:.2f}"
            if op == "/":
                return f"{a / b:.2f}" if b != 0 else None
            if op == "**":
                result = a**b
                if abs(result) > 1e15:
                    return None
                return f"{result:.2f}"
        except (OverflowError, ZeroDivisionError):
            return None

    # Try chained: a op b op c (left-to-right, no precedence)
    parts = re.findall(r"(-?\d+(?:\.\d+)?|[+\-*/])", expr)
    if len(parts) >= 3 and len(parts) % 2 == 1:
        try:
            result = float(parts[0])
            for idx in range(1, len(parts), 2):
                op = parts[idx]
                operand = float(parts[idx + 1])
                if op == "+":
                    result += operand
                elif op == "-":
                    result -= operand
                elif op == "*":
                    result *= operand
                elif op == "/":
                    if operand == 0:
                        return None
                    result /= operand
            if abs(result) > 1e15:
                return None
            return f"{result:.2f}"
        except (ValueError, IndexError, OverflowError):
            return None

    return None


def solve_arithmetic(text: str) -> str | None:
    """Programmatic arithmetic solver — fast-path before LLM.

    Tries three strategies in order:
    1. Digit expression (32 + 18 → 50.00)
    2. Mixed: digit numbers with word operator ('32 plus 18')
    3. Word numbers with word operator ('twenty plus three')

    Returns formatted answer string or None if not solvable.
    """
    # Strategy 1: Pure digit expression
    result = _solve_digit_expression(text)
    if result is not None:
        return result

    # Strategy 2+3: Extract numbers (both digit and word forms)
    # First, try to find digit numbers in the text
    digit_numbers = [int(m) for m in re.findall(r"\b(\d+)\b", text)]
    word_numbers = _extract_word_numbers(text)

    # Use whichever found numbers (prefer digits if both found)
    numbers = digit_numbers if digit_numbers else word_numbers

    # Confidence guard: if using word numbers and ALL are < 20 in a long text,
    # we likely missed a tens-word due to obfuscation (e.g. "t wen ty" → lost "twenty",
    # only found "five"). Bail out and let the LLM handle it.
    if not digit_numbers and numbers and len(text) > 60 and all(n < 20 for n in numbers):
        return None

    if len(numbers) >= 2:
        op = _detect_operation(text)
        computed = _compute([float(n) for n in numbers], op)
        if computed is not None and abs(computed) < 1e15:
            return f"{computed:.2f}"

    # Hybrid: mix digit and word numbers
    if digit_numbers and word_numbers and len(digit_numbers) + len(word_numbers) >= 2:
        all_numbers = [float(n) for n in (digit_numbers + word_numbers)]
        op = _detect_operation(text)
        computed = _compute(all_numbers, op)
        if computed is not None and abs(computed) < 1e15:
            return f"{computed:.2f}"

    return None
