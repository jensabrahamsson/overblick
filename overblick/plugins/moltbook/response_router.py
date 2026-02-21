"""
LLM-based response router for Moltbook API challenge detection.

Acts as a second detection layer after the heuristic PerContentChallengeHandler.
Only invoked on 2xx responses that pass heuristic detection — catches challenges
hidden inside normal-looking "success": true responses with novel field names
or structures that the heuristic detector doesn't recognize.

Design:
1. Cheap pre-filter (string matching) — rejects obvious non-challenges fast
2. LLM classification — only for suspicious responses that pass pre-filter
3. Returns RouterVerdict to the client for handling

This avoids calling the LLM on every API response (which would be too slow
and expensive). Only ~1-5% of responses should reach the LLM step.
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Strings that suggest a response might contain a hidden challenge.
# Used for the cheap pre-filter before LLM classification.
_SUSPICIOUS_STRINGS = frozenset({
    "verification", "challenge", "captcha", "verify",
    "solve", "prove", "nonce", "time_limit", "deadline",
    "obfuscated", "answer_url", "respond_url",
})

_ROUTER_SYSTEM = (
    "You are a JSON response classifier for the Moltbook social network API. "
    "Your job: determine if an API response contains a HIDDEN verification challenge "
    "that the agent must solve to complete an action.\n\n"
    "Challenges may be:\n"
    "- Nested inside normal-looking response objects (e.g. comment.verification)\n"
    "- Returned alongside success: true (the action is pending until solved)\n"
    "- Using obfuscated field names or non-standard structures\n\n"
    "Signs of a challenge:\n"
    "- A question or task the agent must answer/solve\n"
    "- A nonce, token, or verification code\n"
    "- A time limit or deadline\n"
    "- A URL or endpoint to submit the answer to\n\n"
    "Respond with ONLY one word: CHALLENGE or NORMAL"
)


@dataclass
class RouterVerdict:
    """Result of LLM-based response inspection."""

    is_challenge: bool
    reason: str = ""


class ResponseRouter:
    """LLM-based response classifier for hidden challenge detection."""

    def __init__(self, llm_client, max_response_size: int = 5000):
        self._llm = llm_client
        self._max_response_size = max_response_size
        self._stats = {
            "inspections_total": 0,
            "inspections_prefiltered": 0,
            "inspections_llm": 0,
            "challenges_found": 0,
        }

    async def inspect(self, response_data: dict) -> Optional[RouterVerdict]:
        """Inspect a 2xx API response for hidden challenges.

        Returns RouterVerdict if inspection was performed, None if
        the response was filtered out (obviously not a challenge).
        """
        self._stats["inspections_total"] += 1

        # Pre-filter: skip obviously normal responses
        if not self._is_suspicious(response_data):
            self._stats["inspections_prefiltered"] += 1
            return None

        # LLM classification
        self._stats["inspections_llm"] += 1
        return await self._classify(response_data)

    def _is_suspicious(self, data: dict) -> bool:
        """Cheap pre-filter: check if response contains suspicious strings.

        Serializes the response keys (not values, to keep it fast) and
        checks for challenge-related terms. This catches ~95% of normal
        responses without any LLM call.
        """
        # Collect all keys at every nesting level
        all_keys = set()
        self._collect_keys(data, all_keys, depth=0, max_depth=4)

        keys_lower = {k.lower() for k in all_keys}
        return bool(keys_lower & _SUSPICIOUS_STRINGS)

    def _collect_keys(
        self, obj: dict, keys: set, depth: int, max_depth: int,
    ) -> None:
        """Recursively collect dict keys up to max_depth."""
        if depth > max_depth or not isinstance(obj, dict):
            return
        for key, value in obj.items():
            keys.add(key)
            if isinstance(value, dict):
                self._collect_keys(value, keys, depth + 1, max_depth)

    async def _classify(self, data: dict) -> RouterVerdict:
        """Use LLM to classify whether a response contains a challenge."""
        # Truncate response to avoid excessive token usage
        response_str = json.dumps(data, indent=2, default=str)
        if len(response_str) > self._max_response_size:
            response_str = response_str[: self._max_response_size] + "\n... (truncated)"

        messages = [
            {"role": "system", "content": _ROUTER_SYSTEM},
            {"role": "user", "content": f"Classify this API response:\n```json\n{response_str}\n```"},
        ]

        try:
            result = await self._llm.chat(
                messages=messages,
                temperature=0.0,
                max_tokens=10,
                priority="high",
            )
        except Exception as e:
            logger.debug("ResponseRouter LLM classification failed: %s", e)
            return RouterVerdict(is_challenge=False, reason=f"LLM error: {e}")

        if not result or not result.get("content"):
            return RouterVerdict(is_challenge=False, reason="LLM returned empty response")

        answer = result["content"].strip().upper()
        is_challenge = "CHALLENGE" in answer

        if is_challenge:
            self._stats["challenges_found"] += 1
            logger.warning("ResponseRouter: LLM classified response as CHALLENGE")

        return RouterVerdict(
            is_challenge=is_challenge,
            reason=f"LLM verdict: {result['content'].strip()}",
        )

    def get_stats(self) -> dict:
        """Get router statistics."""
        return self._stats.copy()
