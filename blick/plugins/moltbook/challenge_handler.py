"""
Per-content challenge handler for Moltbook API verification.

Intercepts challenge responses, solves them via LLM, and submits
answers within the time limit. Integrates with ResponseRouter for
transparent challenge detection in both 2xx and 4xx responses.

See: https://github.com/moltbook/api/issues/134
"""

import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

CHALLENGE_SOLVER_SYSTEM = """You are a verification challenge solver. You will receive a question that may contain:
- Intentionally misspelled or obfuscated English words
- Arithmetic operations (addition, subtraction, multiplication, exponents)
- Mathematical operations (vector addition, matrix operations, dot products)
- Logic puzzles or simple reasoning questions

Rules:
1. First, decode any obfuscated spelling to understand the actual question
2. Perform the required calculation precisely
3. Return ONLY the final answer — no explanation, no working, no extra text
4. If the answer is a number, return just the number
5. If the answer is a vector, return it as [x, y, z]
6. If the answer is a word or phrase, return just that
7. Be concise and exact — the answer will be compared programmatically"""


class PerContentChallengeHandler:
    """Handles per-content verification challenges from Moltbook API."""

    CHALLENGE_FIELDS = ("challenge", "nonce", "verification", "solve", "question")
    QUESTION_FIELDS = ("question", "challenge_text", "prompt", "challenge", "task")
    NONCE_FIELDS = ("nonce", "challenge_nonce", "token", "challenge_id")
    ENDPOINT_FIELDS = ("respond_url", "answer_url", "callback", "endpoint", "submit_url", "verification_url")
    TIME_LIMIT_FIELDS = ("time_limit", "timeout", "expires_in", "ttl", "deadline_seconds")

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
        """Check if a response contains a per-content challenge."""
        if not isinstance(response_data, dict):
            return False

        keys_lower = {k.lower() for k in response_data.keys()}

        has_nonce = any(nf in keys_lower for nf in self.NONCE_FIELDS)
        has_question = any(qf in keys_lower for qf in self.QUESTION_FIELDS)
        has_endpoint = any(ef in keys_lower for ef in self.ENDPOINT_FIELDS)

        resp_type = str(response_data.get("type", "")).lower()
        is_typed_challenge = resp_type in ("challenge", "verification", "captcha")

        if has_nonce and has_question:
            return True
        if is_typed_challenge:
            return True
        if has_question and has_endpoint:
            return True
        if has_nonce and has_endpoint:
            return True

        # Check nested challenge objects
        for key in ("challenge", "verification", "captcha"):
            nested = response_data.get(key)
            if isinstance(nested, dict):
                nested_keys = {k.lower() for k in nested.keys()}
                if any(qf in nested_keys for qf in self.QUESTION_FIELDS):
                    return True
                if any(nf in nested_keys for nf in self.NONCE_FIELDS):
                    return True

        return False

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

        # Solve via LLM
        messages = [
            {"role": "system", "content": CHALLENGE_SOLVER_SYSTEM},
            {"role": "user", "content": question},
        ]

        llm_timeout = min(self._timeout, (time_limit - 10) if time_limit else self._timeout)
        llm_timeout = max(llm_timeout, 10)

        try:
            result = await self._llm.chat(messages=messages, temperature=0.3, max_tokens=200)
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
        """Extract a field value trying multiple possible names."""
        for name in field_names:
            for key, value in data.items():
                if key.lower() == name and value is not None:
                    return str(value)

        for nest_key in ("challenge", "verification", "captcha"):
            nested = data.get(nest_key)
            if isinstance(nested, dict):
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

        url = endpoint if endpoint.startswith("http") else f"{self._base_url}{endpoint}"

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

    def set_session(self, session: aiohttp.ClientSession) -> None:
        """Set the HTTP session."""
        self._session = session

    def get_stats(self) -> dict:
        """Get challenge handler statistics."""
        return self._stats.copy()
