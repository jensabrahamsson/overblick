"""
Per-content challenge handler for Moltbook API verification.

Intercepts challenge responses, solves them via LLM, and submits
answers within the time limit. Integrates with ResponseRouter for
transparent challenge detection in both 2xx and 4xx responses.

Community findings (issue #134):
- Challenge data nested at data.comment.verification (not top-level)
- Field names: challenge_text, verification_code
- API returns "success": true even during pending verification
- Obfuscation: case-mixing (tWeNtY) + letter-doubling (tWwEeNnTtYy)
- Word-form numbers (twenty three → 23)
- 5-minute time limit

See: https://github.com/moltbook/api/issues/134
"""

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
# Moltbook challenges use two obfuscation techniques (per community reports):
#   1. Case-mixing: random upper/lower casing  →  tWeNtY → twenty
#   2. Letter-doubling: each char repeated with case swap → tWwEeNnTtYy → twenty
# We strip doubling first, then normalize case, before sending to the LLM.

_LETTER_DOUBLE_RE = re.compile(r"[a-zA-Z]")


def _strip_letter_doubling(word: str) -> str:
    """Remove letter-doubling obfuscation from a single word.

    Greedy scan: whenever two adjacent characters are the same letter
    (case-insensitive), keep the first and skip the second.

    Example: tWwEeNnTtYy → tWENTY (doubles at positions 1-2, 3-4, etc.)
    Example: tTwWeEnNtTyY → twenty (doubles at positions 0-1, 2-3, etc.)

    Only returns the cleaned result if it removed a significant portion
    of the word (>25% shorter), to avoid false positives on normal words
    with natural letter repetition (e.g. "llama", "hello").
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
        ):
            i += 2  # Skip the doubled char
        else:
            i += 1

    cleaned = "".join(result)
    # Only apply if we removed a significant number of chars (>25%)
    if len(cleaned) <= len(word) * 0.75:
        return cleaned
    return word


def deobfuscate_challenge(text: str) -> str:
    """Remove case-mixing and letter-doubling from challenge text.

    Processes each word independently:
    1. Strip letter-doubling (tWwEeNnTtYy → tWENTY)
    2. Normalize to lowercase (tWENTY → twenty)

    Non-alphabetic tokens (numbers, punctuation, operators) are preserved as-is.
    """
    tokens = text.split()
    result = []
    for token in tokens:
        # Preserve non-alpha tokens (numbers, operators, punctuation)
        if not any(c.isalpha() for c in token):
            result.append(token)
            continue

        # Split trailing punctuation from word
        word = token
        trailing = ""
        while word and not word[-1].isalpha():
            trailing = word[-1] + trailing
            word = word[:-1]
        leading = ""
        while word and not word[0].isalpha():
            leading += word[0]
            word = word[1:]

        if word:
            cleaned = _strip_letter_doubling(word)
            result.append(f"{leading}{cleaned.lower()}{trailing}")
        else:
            result.append(token)

    return " ".join(result)


class PerContentChallengeHandler:
    """Handles per-content verification challenges from Moltbook API."""

    CHALLENGE_FIELDS = ("challenge", "nonce", "verification", "solve", "question")
    QUESTION_FIELDS = ("question", "challenge_text", "prompt", "challenge", "task", "instructions")
    NONCE_FIELDS = ("nonce", "challenge_nonce", "token", "challenge_id", "verification_code", "code")
    ENDPOINT_FIELDS = ("respond_url", "answer_url", "callback", "endpoint", "submit_url", "verification_url")
    TIME_LIMIT_FIELDS = ("time_limit", "timeout", "expires_in", "ttl", "deadline_seconds")

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
    ):
        self._llm = llm_client
        self._session = http_session
        self._api_key = api_key
        self._base_url = base_url.rstrip("/") if base_url else ""
        self._timeout = timeout
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

    async def solve(self, challenge_data: dict) -> Optional[dict]:
        """Solve a challenge and submit the answer."""
        start_time = time.monotonic()
        self._stats["challenges_detected"] += 1

        question = self._extract_field(challenge_data, self.QUESTION_FIELDS)
        nonce = self._extract_field(challenge_data, self.NONCE_FIELDS)
        endpoint = self._extract_field(challenge_data, self.ENDPOINT_FIELDS)
        time_limit = self._extract_time_limit(challenge_data)

        logger.warning(
            "PER-CONTENT CHALLENGE: question=%s | nonce=%s | endpoint=%s | time_limit=%s",
            question[:200] if question else None, nonce, endpoint, time_limit,
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
                question[:100], clean_question[:100],
            )

        # Solve via LLM
        messages = [
            {"role": "system", "content": CHALLENGE_SOLVER_SYSTEM},
            {"role": "user", "content": clean_question},
        ]

        try:
            result = await self._llm.chat(messages=messages, temperature=0.3, max_tokens=200, priority="high")
        except Exception as e:
            logger.error("CHALLENGE: LLM error: %s", e, exc_info=True)
            self._stats["challenges_failed"] += 1
            return None

        if not result or not result.get("content"):
            logger.error("CHALLENGE: LLM returned empty response")
            self._stats["challenges_failed"] += 1
            return None

        answer = result["content"].strip()
        elapsed = time.monotonic() - start_time
        logger.info("CHALLENGE: LLM answered in %.1fs: '%s' -> '%s'", elapsed, question[:100], answer)

        if not endpoint:
            logger.warning("CHALLENGE: No endpoint found, cannot submit")
            self._stats["challenges_failed"] += 1
            return None

        # Submit answer
        submit_result = await self._submit_answer(endpoint, answer, nonce, challenge_data)

        if submit_result is not None:
            total_elapsed = time.monotonic() - start_time
            logger.info("CHALLENGE: Solved in %.1fs (limit: %ss)", total_elapsed, time_limit)
            self._stats["challenges_solved"] += 1
        else:
            self._stats["challenges_failed"] += 1

        return submit_result

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
        self, endpoint: str, answer: str, nonce: Optional[str], challenge_data: dict,
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
                    url, self._base_url,
                )
                return await self._submit_answer_no_auth(url, answer, nonce, challenge_data)
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
                url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                raw_body = await response.text()
                logger.info("CHALLENGE: Response HTTP %d: %s", response.status, raw_body[:1000])

                if response.status >= 400:
                    logger.error("CHALLENGE: Rejected HTTP %d: %s", response.status, raw_body)
                    return None

                try:
                    return await response.json(content_type=None)
                except Exception:
                    return {"success": True, "raw_response": raw_body}

        except Exception as e:
            logger.error("CHALLENGE: Submission failed: %s", e, exc_info=True)
            return None

    async def _submit_answer_no_auth(
        self, url: str, answer: str, nonce: Optional[str], challenge_data: dict,
    ) -> Optional[dict]:
        """Submit without auth header (external endpoint — no API key leakage)."""
        payload = {"answer": answer}
        if nonce:
            payload["nonce"] = nonce
        challenge_id = challenge_data.get("challenge_id") or challenge_data.get("id")
        if challenge_id:
            payload["challenge_id"] = str(challenge_id)

        try:
            async with self._session.post(
                url, json=payload, headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                raw_body = await response.text()
                logger.info("CHALLENGE: Response HTTP %d: %s", response.status, raw_body[:1000])
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
