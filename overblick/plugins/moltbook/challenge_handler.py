"""
Per-content challenge handler for Moltbook API verification.

Intercepts challenge responses, solves them via LLM, and submits
answers within the time limit. Integrates with ResponseRouter for
transparent challenge detection in both 2xx and 4xx responses.

Community findings (issue #134):
- Challenge data nested at data.comment.verification (not top-level)
- Field names: challenge_text, verification_code
- API returns "success": true even during pending verification
- Obfuscation: case-mixing (tWeNtY) + letter-doubling (tWwEeNnTtYy) + space injection (f i v e)
- Word-form numbers (twenty three → 23)
- 5-minute time limit

See: https://github.com/moltbook/api/issues/134
"""

import json as json_module
import logging
import re
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

CHALLENGE_SOLVER_SYSTEM = """You are a verification challenge solver. The question has already been \
deobfuscated (letter-doubling and case-mixing removed). You may receive:
- Arithmetic operations (addition, subtraction, multiplication, exponents)
- Word-form numbers (e.g. "twenty three" means 23)
- Mathematical operations (vector addition, matrix operations, dot products)
- Logic puzzles or simple reasoning questions

Rules:
1. Parse word-form numbers into digits (e.g. "twenty three" = 23)
2. Perform the required calculation precisely
3. Return ONLY the final answer — no explanation, no working, no extra text
4. If the answer is a number, return just the number
5. If the answer is a vector, return it as [x, y, z]
6. If the answer is a word or phrase, return just that
7. Be concise and exact — the answer will be compared programmatically"""


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


# ── Programmatic arithmetic solver ───────────────────────────────────────────
# Fast-path solver for arithmetic challenges. Runs before any LLM call.
# Based on community implementation (moltbook-mcp by p4stoboy).
# Three strategies: digit expressions, operator split, word numbers.
# NOTE: No eval() — all arithmetic is done via safe manual parsing.

# Word-form number dictionaries
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

# ── Fragment reassembly for space-injection obfuscation ─────────────────────
# Moltbook splits words with spaces and noise chars: "f i v e" instead of "five".
# After per-token deobfuscation, we greedily merge short fragments back into
# known words (number words + challenge domain vocabulary).

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


def _fuzzy_match(word: str, dictionary: dict[str, int]) -> Optional[tuple[str, int]]:
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
        if i + 1 < len(tokens):
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

        # Try single token
        tens_match = _fuzzy_match(tokens[i], _TENS)
        if tens_match:
            numbers.append(tens_match[1])
            i += 1
            continue

        ones_match = _fuzzy_match(tokens[i], _ONES)
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


def _compute(numbers: list[int | float], op: str) -> Optional[float]:
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


def _solve_digit_expression(text: str) -> Optional[str]:
    """Solve pure digit arithmetic expressions like '32 + 18' or '5 * 3'.

    Uses safe manual parsing — no dynamic code execution.
    Supports +, -, *, /, ^.
    """
    matches = _DIGIT_EXPR_RE.findall(text)
    if not matches:
        return None

    # Pick the longest candidate containing an operator
    candidates = [
        m.strip() for m in matches if re.search(r"[+\-*/^]", m) and re.search(r"\d", m)
    ]
    if not candidates:
        return None

    expr = max(candidates, key=len)
    if len(expr) > 200:
        return None

    expr = expr.replace("^", "**")

    # Try simple binary: a op b
    m = re.match(
        r"^\s*(-?\d+(?:\.\d+)?)\s*([+\-*/]|\*\*)\s*(-?\d+(?:\.\d+)?)\s*$", expr
    )
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


def solve_arithmetic(text: str) -> Optional[str]:
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
    if (
        not digit_numbers
        and numbers
        and len(text) > 60
        and all(n < 20 for n in numbers)
    ):
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


class PerContentChallengeHandler:
    """Handles per-content verification challenges from Moltbook API."""

    CHALLENGE_FIELDS = ("challenge", "nonce", "verification", "solve", "question")
    QUESTION_FIELDS = (
        "question",
        "challenge_text",
        "prompt",
        "challenge",
        "task",
        "instructions",
    )
    NONCE_FIELDS = (
        "nonce",
        "challenge_nonce",
        "token",
        "challenge_id",
        "verification_code",
        "code",
    )
    ENDPOINT_FIELDS = (
        "respond_url",
        "answer_url",
        "callback",
        "endpoint",
        "submit_url",
        "verification_url",
    )
    TIME_LIMIT_FIELDS = (
        "time_limit",
        "timeout",
        "expires_in",
        "ttl",
        "deadline_seconds",
    )

    # Nesting paths where challenge data may be located (issue #134 community findings).
    # Each path is a tuple of dict keys to traverse from the top-level response.
    NESTING_PATHS = (
        ("challenge",),
        ("verification",),
        ("captcha",),
        ("comment", "verification"),
        ("post", "verification"),
        ("data", "verification"),
        ("data", "challenge"),
    )

    def __init__(
        self,
        llm_client,
        http_session: Optional[aiohttp.ClientSession] = None,
        api_key: str = "",
        base_url: str = "",
        timeout: int = 45,
        audit_log=None,
        engagement_db=None,
    ):
        self._llm = llm_client
        self._session = http_session
        self._api_key = api_key
        self._base_url = base_url.rstrip("/") if base_url else ""
        self._timeout = timeout
        self._audit_log = audit_log
        self._engagement_db = engagement_db
        self._stats = {
            "challenges_detected": 0,
            "challenges_solved": 0,
            "challenges_failed": 0,
            "challenges_timeout": 0,
        }

    def detect(self, response_data: dict, http_status: int) -> bool:
        """Check if a response contains a per-content challenge.

        Searches both top-level keys and nested paths (e.g. data.comment.verification)
        since the API may embed challenges at various depths.
        """
        if not isinstance(response_data, dict):
            return False

        # Explicit flag from the API spec (top-level indicator)
        if response_data.get("verification_required"):
            return True

        # Check top-level fields
        if self._check_challenge_fields(response_data):
            return True

        resp_type = str(response_data.get("type", "")).lower()
        if resp_type in ("challenge", "verification", "captcha"):
            return True

        # Check all known nesting paths
        for path in self.NESTING_PATHS:
            nested = self._traverse_path(response_data, path)
            if nested is not None and self._check_challenge_fields(nested):
                return True

        return False

    def _check_challenge_fields(self, data: dict) -> bool:
        """Check if a dict contains challenge indicator fields."""
        if not isinstance(data, dict):
            return False
        keys_lower = {k.lower() for k in data.keys()}
        has_nonce = any(nf in keys_lower for nf in self.NONCE_FIELDS)
        has_question = any(qf in keys_lower for qf in self.QUESTION_FIELDS)
        has_endpoint = any(ef in keys_lower for ef in self.ENDPOINT_FIELDS)
        if has_nonce and has_question:
            return True
        if has_question and has_endpoint:
            return True
        if has_nonce and has_endpoint:
            return True
        return False

    @staticmethod
    def _traverse_path(data: dict, path: tuple[str, ...]) -> Optional[dict]:
        """Traverse a nested dict path, returning the final dict or None."""
        current = data
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current if isinstance(current, dict) else None

    async def solve(
        self,
        challenge_data: dict,
        original_endpoint: Optional[str] = None,
        original_payload: Optional[dict] = None,
    ) -> Optional[dict]:
        """Solve a challenge and submit the answer.

        Args:
            challenge_data: The challenge response from the API.
            original_endpoint: The endpoint that triggered the challenge (for retry submission).
            original_payload: The original POST body (for retry submission with verification fields).
        """
        start_time = time.monotonic()
        self._stats["challenges_detected"] += 1

        question = self._extract_field(challenge_data, self.QUESTION_FIELDS)
        nonce = self._extract_field(challenge_data, self.NONCE_FIELDS)
        endpoint = self._extract_field(challenge_data, self.ENDPOINT_FIELDS)
        time_limit = self._extract_time_limit(challenge_data)

        logger.warning(
            "PER-CONTENT CHALLENGE: question=%s | nonce=%s | endpoint=%s | time_limit=%s",
            question[:200] if question else None,
            nonce,
            endpoint,
            time_limit,
        )

        # Audit: challenge_received
        self._audit(
            "challenge_received",
            {
                "question_raw": question[:500] if question else None,
                "nonce": nonce,
                "has_submit_url": bool(endpoint),
                "has_original_endpoint": bool(original_endpoint),
            },
        )

        if not question:
            logger.error("CHALLENGE: No question found in data: %s", challenge_data)
            self._stats["challenges_failed"] += 1
            return None

        # Deobfuscate before sending to LLM
        clean_question = deobfuscate_challenge(question)
        if clean_question != question:
            logger.info(
                "CHALLENGE: Deobfuscated: '%s' -> '%s'",
                question[:100],
                clean_question[:100],
            )

        # Solve: cloud LLM (Devstral) → local LLM → arithmetic fallback
        # LLM-first because obfuscated word problems defeat the arithmetic
        # parser (split words like "t wen ty" produce wrong answers).
        # Arithmetic is kept as last-resort backup if both LLMs are down.
        answer = None
        solver = None

        answer = await self._solve_with_llm(clean_question, complexity="ultra")
        if answer:
            solver = "cloud_llm"

        if not answer:
            answer = await self._solve_with_llm(clean_question, complexity="low")
            if answer:
                solver = "local_llm"

        # Validate LLM answers against arithmetic solver for math challenges
        if answer and solver and solver.endswith("_llm"):
            arithmetic_answer = solve_arithmetic(clean_question)
            if arithmetic_answer:
                # If answers differ, prefer the arithmetic solution (more reliable for math)
                if self._answers_differ(answer, arithmetic_answer):
                    logger.warning(
                        "CHALLENGE: LLM answer '%s' differs from arithmetic '%s', using arithmetic",
                        answer,
                        arithmetic_answer,
                    )
                    answer = arithmetic_answer
                    solver = "arithmetic_fallback"

        if not answer:
            answer = solve_arithmetic(clean_question)
            if answer:
                solver = "arithmetic"

        elapsed = time.monotonic() - start_time

        if not answer:
            logger.error("CHALLENGE: All solvers failed for: %s", clean_question[:200])
            self._stats["challenges_failed"] += 1
            self._audit(
                "challenge_response",
                {
                    "question_clean": clean_question[:500],
                    "answer": None,
                    "solver": None,
                    "duration_ms": round(elapsed * 1000, 1),
                    "error": "all_solvers_failed",
                },
            )
            return None

        logger.info(
            "CHALLENGE: %s answered in %.1fs: '%s' -> '%s'",
            solver,
            elapsed,
            clean_question[:100],
            answer,
        )

        # Audit: challenge_response
        self._audit(
            "challenge_response",
            {
                "question_clean": clean_question[:500],
                "answer": answer,
                "solver": solver,
                "duration_ms": round(elapsed * 1000, 1),
            },
        )

        # Submit answer via multi-strategy
        submit_result = await self._try_submit(
            answer=answer,
            nonce=nonce,
            challenge_data=challenge_data,
            endpoint=endpoint,
            original_endpoint=original_endpoint,
            original_payload=original_payload,
        )

        total_elapsed = time.monotonic() - start_time

        if submit_result is not None:
            logger.info(
                "CHALLENGE: Solved in %.1fs (limit: %ss)", total_elapsed, time_limit
            )
            self._stats["challenges_solved"] += 1
        else:
            self._stats["challenges_failed"] += 1

        # Record to DB
        await self._record_challenge(
            challenge_id=nonce,
            question_raw=question,
            question_clean=clean_question,
            answer=answer,
            solver=solver,
            correct=submit_result is not None,
            endpoint=endpoint or original_endpoint,
            duration_ms=round(total_elapsed * 1000, 1),
            http_status=submit_result.get("_http_status")
            if isinstance(submit_result, dict)
            else None,
            error=None if submit_result else "submit_failed",
        )

        return submit_result

    async def _solve_with_llm(
        self, clean_question: str, complexity: str = "low"
    ) -> Optional[str]:
        """Solve challenge via LLM Gateway with specified complexity routing."""
        messages = [
            {"role": "system", "content": CHALLENGE_SOLVER_SYSTEM},
            {"role": "user", "content": clean_question},
        ]
        try:
            result = await self._llm.chat(
                messages=messages,
                temperature=0.0,
                max_tokens=512,
                priority="high",
                complexity=complexity,
            )
        except Exception as e:
            logger.warning("CHALLENGE: LLM (complexity=%s) failed: %s", complexity, e)
            return None

        if not result or not result.get("content"):
            return None
        return result["content"].strip()

    async def _try_submit(
        self,
        answer: str,
        nonce: Optional[str],
        challenge_data: dict,
        endpoint: Optional[str],
        original_endpoint: Optional[str],
        original_payload: Optional[dict],
    ) -> Optional[dict]:
        """Multi-strategy challenge submission.

        Tries strategies in order, returns the first successful result:
        1. POST /verify — the known standard endpoint (from moltbook-mcp community)
        2. Explicit endpoint from challenge data (if provided and different)
        3. Retry original POST with verification fields injected

        Numeric answers are formatted to 2 decimal places per API convention.
        All attempts are logged for forensic analysis (issue #134, #168).
        """
        # Format numeric answers to 2 decimal places (API convention)
        formatted_answer = self._format_answer(answer)
        strategies_tried = []

        # Strategy 1: POST /verify — the known standard endpoint
        if self._base_url and self._session:
            verify_payload: dict = {"answer": formatted_answer}
            if nonce:
                verify_payload["verification_code"] = nonce
            challenge_id = challenge_data.get("challenge_id") or challenge_data.get(
                "id"
            )
            if challenge_id:
                verify_payload["challenge_id"] = str(challenge_id)

            result = await self._post_with_logging(
                "/verify",
                verify_payload,
                "post_verify",
            )
            strategies_tried.append(("post_verify", "/verify", result is not None))
            if result is not None:
                self._audit(
                    "challenge_submitted",
                    {
                        "nonce": nonce,
                        "answer": formatted_answer,
                        "method": "post_verify",
                        "success": True,
                    },
                )
                return result

        # Strategy 2: Explicit submit endpoint from challenge data (if different from /verify)
        if endpoint and endpoint != "/verify":
            result = await self._submit_answer(
                endpoint, formatted_answer, nonce, challenge_data
            )
            strategies_tried.append(("explicit_endpoint", endpoint, result is not None))
            if result is not None:
                self._audit(
                    "challenge_submitted",
                    {
                        "nonce": nonce,
                        "answer": formatted_answer,
                        "method": "explicit_endpoint",
                        "success": True,
                    },
                )
                return result

        # Strategy 3: Retry original POST with verification fields
        if original_endpoint and self._session:
            retry_payload = dict(original_payload) if original_payload else {}
            retry_payload["verification_answer"] = formatted_answer
            if nonce:
                retry_payload["verification_code"] = nonce

            result = await self._post_with_logging(
                original_endpoint,
                retry_payload,
                "retry_original",
            )
            strategies_tried.append(
                ("retry_original", original_endpoint, result is not None)
            )
            if result is not None:
                self._audit(
                    "challenge_submitted",
                    {
                        "nonce": nonce,
                        "answer": formatted_answer,
                        "method": "retry_original",
                        "success": True,
                    },
                )
                return result

        # All strategies failed
        logger.error(
            "CHALLENGE: All submit strategies failed: %s",
            [(s[0], s[1], s[2]) for s in strategies_tried],
        )
        self._audit(
            "challenge_submitted",
            {
                "nonce": nonce,
                "answer": formatted_answer,
                "method": "all_failed",
                "success": False,
                "strategies_tried": [s[0] for s in strategies_tried],
            },
        )
        return None

    @staticmethod
    def _format_answer(answer: str) -> str:
        """Format answer for API submission.

        Numeric answers are formatted to 2 decimal places per Moltbook API convention
        (confirmed by moltbook-mcp community implementation).
        """
        try:
            num = float(answer)
            if not (num != num):  # not NaN
                return f"{num:.2f}"
        except (ValueError, TypeError):
            pass
        return answer

    async def _post_with_logging(
        self,
        url: str,
        payload: dict,
        strategy_name: str,
    ) -> Optional[dict]:
        """POST with full forensic logging for submit strategies."""
        if not self._session:
            return None

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with self._session.post(
                url if url.startswith("http") else f"{self._base_url}{url}",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                raw_body = await response.text()
                logger.info(
                    "CHALLENGE SUBMIT [%s]: %s -> HTTP %d | headers=%s | body=%.1000s",
                    strategy_name,
                    url,
                    response.status,
                    {
                        k: v
                        for k, v in response.headers.items()
                        if k.lower().startswith("x-") or "verif" in k.lower()
                    },
                    raw_body,
                )
                if response.status >= 400:
                    return None
                try:
                    result = json_module.loads(raw_body)
                    result["_http_status"] = response.status
                    return result
                except Exception:
                    return {
                        "success": True,
                        "raw_response": raw_body,
                        "_http_status": response.status,
                    }
        except Exception as e:
            logger.warning("CHALLENGE SUBMIT [%s] failed: %s", strategy_name, e)
            return None

    @staticmethod
    def _answers_differ(llm_answer: str, arithmetic_answer: str) -> bool:
        """Check if LLM and arithmetic answers differ significantly."""
        try:
            # Parse both answers as numbers
            llm_num = float(llm_answer.replace(",", "").replace(" ", ""))
            arith_num = float(arithmetic_answer.replace(",", "").replace(" ", ""))

            # Consider them different if more than 0.01 apart
            return abs(llm_num - arith_num) > 0.01
        except (ValueError, AttributeError):
            # If we can't parse as numbers, they're different
            return llm_answer != arithmetic_answer

    def _extract_field(self, data: dict, field_names: tuple) -> Optional[str]:
        """Extract a field value trying multiple possible names and nesting paths."""
        # Check top-level
        for name in field_names:
            for key, value in data.items():
                if key.lower() == name and value is not None:
                    return str(value)

        # Check all nesting paths
        for path in self.NESTING_PATHS:
            nested = self._traverse_path(data, path)
            if nested is not None:
                for name in field_names:
                    for key, value in nested.items():
                        if key.lower() == name and value is not None:
                            return str(value)

        return None

    def _extract_time_limit(self, data: dict) -> Optional[int]:
        """Extract time limit from challenge data."""
        raw = self._extract_field(data, self.TIME_LIMIT_FIELDS)
        if raw is not None:
            try:
                return int(float(raw))
            except (ValueError, TypeError):
                pass
        return None

    async def _submit_answer(
        self,
        endpoint: str,
        answer: str,
        nonce: Optional[str],
        challenge_data: dict,
    ) -> Optional[dict]:
        """Submit a challenge answer."""
        if not self._session:
            logger.error("CHALLENGE: No HTTP session available")
            return None

        if endpoint.startswith("http"):
            url = endpoint
            # Prevent API key leakage: only send auth to our own base_url.
            # When base_url is not configured, never send auth to absolute URLs.
            if not self._base_url or not url.startswith(self._base_url):
                logger.warning(
                    "CHALLENGE: Endpoint URL '%s' outside base_url '%s', "
                    "submitting WITHOUT auth header",
                    url,
                    self._base_url,
                )
                return await self._submit_answer_no_auth(
                    url, answer, nonce, challenge_data
                )
        else:
            url = f"{self._base_url}{endpoint}"

        payload = {"answer": answer}
        if nonce:
            payload["nonce"] = nonce

        challenge_id = challenge_data.get("challenge_id") or challenge_data.get("id")
        if challenge_id:
            payload["challenge_id"] = str(challenge_id)

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        logger.info("CHALLENGE: Submitting to %s: %s", url, payload)

        try:
            async with self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                raw_body = await response.text()
                logger.info(
                    "CHALLENGE: Response HTTP %d: %s", response.status, raw_body[:1000]
                )

                if response.status >= 400:
                    logger.error(
                        "CHALLENGE: Rejected HTTP %d: %s", response.status, raw_body
                    )
                    return None

                try:
                    return await response.json(content_type=None)
                except Exception:
                    return {"success": True, "raw_response": raw_body}

        except Exception as e:
            logger.error("CHALLENGE: Submission failed: %s", e, exc_info=True)
            return None

    async def _submit_answer_no_auth(
        self,
        url: str,
        answer: str,
        nonce: Optional[str],
        challenge_data: dict,
    ) -> Optional[dict]:
        """Submit without auth header (external endpoint — no API key leakage)."""
        payload = {"answer": answer}
        if nonce:
            payload["nonce"] = nonce
        challenge_id = challenge_data.get("challenge_id") or challenge_data.get("id")
        if challenge_id:
            payload["challenge_id"] = str(challenge_id)

        try:
            if not self._session:
                logger.error("CHALLENGE: No HTTP session available")
                return None
            async with self._session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                raw_body = await response.text()
                logger.info(
                    "CHALLENGE: Response HTTP %d: %s", response.status, raw_body[:1000]
                )
                if response.status >= 400:
                    return None
                try:
                    return await response.json(content_type=None)
                except Exception:
                    return {"success": True, "raw_response": raw_body}
        except Exception as e:
            logger.error("CHALLENGE: Submission failed: %s", e, exc_info=True)
            return None

    def set_session(self, session: aiohttp.ClientSession) -> None:
        """Set the HTTP session."""
        self._session = session

    def get_stats(self) -> dict:
        """Get challenge handler statistics."""
        return self._stats.copy()

    # ── Audit & DB helpers ───────────────────────────────────────────────

    def _audit(self, action: str, details: dict) -> None:
        """Log an audit event if audit_log is available."""
        if self._audit_log:
            try:
                self._audit_log.log(action=action, details=details)
            except Exception as e:
                logger.debug("Audit log error: %s", e)

    async def _record_challenge(
        self,
        challenge_id: Optional[str],
        question_raw: Optional[str],
        question_clean: Optional[str],
        answer: Optional[str],
        solver: Optional[str],
        correct: bool,
        endpoint: Optional[str],
        duration_ms: float,
        http_status: Optional[int],
        error: Optional[str],
    ) -> None:
        """Record challenge attempt to engagement DB if available."""
        if not self._engagement_db:
            return
        try:
            await self._engagement_db.record_challenge(
                challenge_id=challenge_id,
                question_raw=question_raw,
                question_clean=question_clean,
                answer=answer,
                solver=solver,
                correct=correct,
                endpoint=endpoint,
                duration_ms=duration_ms,
                http_status=http_status,
                error=error,
            )
        except Exception as e:
            logger.debug("Challenge DB record error: %s", e)
