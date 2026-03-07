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

Architecture:
- Deobfuscation engine: overblick.plugins.moltbook.deobfuscator
- Arithmetic solver: overblick.plugins.moltbook.arithmetic_solver
- Challenge handler (this module): orchestrator that uses the above two
"""

import json as json_module
import logging
import time
from typing import TYPE_CHECKING, Any, Optional, Union

import aiohttp  # type: ignore[import-not-found]

from .arithmetic_solver import (
    _extract_word_numbers,
    _fuzzy_match,
    _is_subsequence,
    solve_arithmetic,
)

# Import from split modules
from .deobfuscator import (
    _ONES,
    _TENS,
    _edit_distance_one,
    _reassemble_fragments,
    _strip_letter_doubling,
    deobfuscate_challenge,
)

if TYPE_CHECKING:
    from overblick.core.db.engagement_db import EngagementDB
    from overblick.core.llm.client import LLMClient
    from overblick.core.llm.pipeline import PipelineResult, SafeLLMPipeline
    from overblick.core.security.audit_log import AuditLog

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

    _llm: Union["SafeLLMPipeline", "LLMClient"]

    def __init__(
        self,
        llm_pipeline: Optional["SafeLLMPipeline"] = None,
        http_session: aiohttp.ClientSession | None = None,
        api_key: str = "",
        base_url: str = "",
        timeout: int = 45,
        audit_log: Optional["AuditLog"] = None,
        engagement_db: Optional["EngagementDB"] = None,
        *,
        llm_client: Optional["LLMClient"] = None,
        allow_raw_fallback: bool = False,
    ) -> None:
        import os

        # Safe-mode enforcement
        if not llm_pipeline:
            if allow_raw_fallback and llm_client:
                logger.warning("ChallengeHandler using raw client (allow_raw_fallback=True)")
                self._llm = llm_client
            else:
                raise ValueError(
                    "SafeLLMPipeline is required in safe mode. "
                    "Provide llm_pipeline or set allow_raw_fallback=True."
                )
        else:
            self._llm = llm_pipeline
            if llm_client:
                logger.debug("Both pipeline and raw client provided; using pipeline")
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
        if not isinstance(response_data, dict):  # type: ignore[unreachable]
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
        if not isinstance(data, dict):  # type: ignore[unreachable]
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
    def _traverse_path(data: dict, path: tuple[str, ...]) -> dict | None:
        """Traverse a nested dict path, returning the final dict or None."""
        current = data
        for key in path:
            if not isinstance(current, dict):  # type: ignore[unreachable]
                return None
            current = current.get(key)  # type: ignore[assignment]
        return current if isinstance(current, dict) else None

    async def solve(
        self,
        challenge_data: dict,
        original_endpoint: str | None = None,
        original_payload: dict | None = None,
    ) -> dict | None:
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

        # Cross-validate LLM vs arithmetic for math challenges.
        # Arithmetic is deterministic but word-number parsing can be wrong with
        # obfuscated text. LLM has better contextual understanding but can
        # miscalculate. Use arithmetic only when its result looks trustworthy
        # (non-negative — negative results often signal parsing errors).
        if answer and solver and solver.endswith("_llm"):
            arithmetic_answer = solve_arithmetic(clean_question)
            if arithmetic_answer and self._answers_differ(answer, arithmetic_answer):
                arith_val = float(arithmetic_answer)
                if arith_val >= 0:
                    logger.warning(
                        "CHALLENGE: LLM answer '%s' differs from arithmetic '%s', using arithmetic",
                        answer,
                        arithmetic_answer,
                    )
                    answer = arithmetic_answer
                    solver = "arithmetic_fallback"
                else:
                    logger.warning(
                        "CHALLENGE: LLM answer '%s' differs from arithmetic '%s' "
                        "(negative — likely parse error), trusting LLM",
                        answer,
                        arithmetic_answer,
                    )

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
            logger.info("CHALLENGE: Solved in %.1fs (limit: %ss)", total_elapsed, time_limit)
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
            http_status=(
                submit_result.get("_http_status") if isinstance(submit_result, dict) else None
            ),
            error=None if submit_result else "submit_failed",
        )

        return submit_result

    async def _solve_with_llm(self, clean_question: str, complexity: str = "low") -> str | None:
        """Solve challenge via LLM Gateway with specified complexity routing."""
        from overblick.core.llm.pipeline import PipelineResult

        messages = [
            {"role": "system", "content": CHALLENGE_SOLVER_SYSTEM},
            {"role": "user", "content": clean_question},
        ]
        try:
            result: PipelineResult | dict | None = await self._llm.chat(
                messages=messages,
                temperature=0.0,
                max_tokens=512,
                priority="high",
                complexity=complexity,
            )
        except Exception as e:
            logger.warning("CHALLENGE: LLM (complexity=%s) failed: %s", complexity, e)
            return None

        # Handle both pipeline result and raw client result
        if isinstance(result, PipelineResult):
            # Pipeline result
            if result.blocked:
                logger.warning(
                    "CHALLENGE: Pipeline blocked at %s: %s",
                    result.block_stage.value if result.block_stage else "unknown",
                    result.block_reason,
                )
                return None
            content = result.content
        else:
            # Raw client result (dict) or None
            if not result or not result.get("content"):
                return None
            content = result["content"]

        if content:
            return content.strip()
        return None

    async def _try_submit(
        self,
        answer: str,
        nonce: str | None,
        challenge_data: dict,
        endpoint: str | None,
        original_endpoint: str | None,
        original_payload: dict | None,
    ) -> dict | None:
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
            challenge_id = challenge_data.get("challenge_id") or challenge_data.get("id")
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
            result = await self._submit_answer(endpoint, formatted_answer, nonce, challenge_data)
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
            strategies_tried.append(("retry_original", original_endpoint, result is not None))
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
    ) -> dict | None:
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

    def _extract_field(self, data: dict, field_names: tuple) -> str | None:
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

    def _extract_time_limit(self, data: dict) -> int | None:
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
        nonce: str | None,
        challenge_data: dict,
    ) -> dict | None:
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
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
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
        self,
        url: str,
        answer: str,
        nonce: str | None,
        challenge_data: dict,
    ) -> dict | None:
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
        challenge_id: str | None,
        question_raw: str | None,
        question_clean: str | None,
        answer: str | None,
        solver: str | None,
        correct: bool,
        endpoint: str | None,
        duration_ms: float,
        http_status: int | None,
        error: str | None,
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
