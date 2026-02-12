"""
Response Router — inspects ALL API responses through LLM.

The killer feature: every Moltbook API response passes through the
response router, which uses LLM (chat mode, fast) to detect:
1. Challenges (MoltCaptcha verification)
2. Suspicious content (potential traps)
3. Anomalies in API responses

Falls back to heuristic detection if LLM is unavailable.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ResponseVerdict(Enum):
    """Classification of an API response."""
    NORMAL = "normal"
    CHALLENGE = "challenge"
    SUSPICIOUS = "suspicious"
    ERROR = "error"


@dataclass
class RouterResult:
    """Result of response inspection."""
    verdict: ResponseVerdict
    confidence: float = 1.0
    details: Optional[dict[str, Any]] = None
    analysis_time_ms: float = 0.0


# Heuristic patterns for challenge detection (fallback when LLM unavailable)
_CHALLENGE_PATTERNS = [
    re.compile(r"moltcaptcha", re.IGNORECASE),
    re.compile(r"verification\s+challenge", re.IGNORECASE),
    re.compile(r"prove\s+you\s+are", re.IGNORECASE),
    re.compile(r"ascii_sum", re.IGNORECASE),
    re.compile(r"word_count\s*[=:]\s*\d+", re.IGNORECASE),
    re.compile(r"respond\s+within\s+\d+\s*s", re.IGNORECASE),
]

_SUSPICIOUS_PATTERNS = [
    re.compile(r"your\s+api\s+key", re.IGNORECASE),
    re.compile(r"send\s+your\s+credentials", re.IGNORECASE),
    re.compile(r"(admin|root)\s+access", re.IGNORECASE),
]

# LLM prompt for response inspection
_INSPECT_PROMPT = """Analyze this API response data. Classify it as one of:
- NORMAL: Regular API response
- CHALLENGE: Contains a verification challenge (MoltCaptcha, puzzle, time-limited task)
- SUSPICIOUS: Contains unusual or potentially malicious content

Response data:
{data}

Reply with ONLY a JSON object:
{{"verdict": "NORMAL|CHALLENGE|SUSPICIOUS", "confidence": 0.0-1.0, "reason": "brief explanation"}}"""


class ResponseRouter:
    """
    Inspects API responses for challenges and anomalies.

    Integrates transparently into the API client's request pipeline.

    Usage:
        router = ResponseRouter(llm_client=ollama)
        result = router.inspect(response_data)
        if result.verdict == ResponseVerdict.CHALLENGE:
            handle_challenge(response_data)
    """

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLM client for intelligent inspection (optional).
                        Falls back to heuristics if None.
        """
        self._llm = llm_client
        self._inspection_count = 0
        self._challenge_count = 0

    def set_llm_client(self, client) -> None:
        """Update the LLM client."""
        self._llm = client

    async def inspect(
        self,
        data: Any,
        use_llm: bool = True,
    ) -> RouterResult:
        """
        Inspect an API response.

        Args:
            data: Response data (dict, list, or string)
            use_llm: Whether to use LLM analysis (set False for speed)

        Returns:
            RouterResult with verdict and details
        """
        start = time.monotonic()
        self._inspection_count += 1

        # Convert to string for pattern matching
        text = self._to_text(data)

        # Fast heuristic check first
        heuristic_result = self._heuristic_check(text)
        if heuristic_result.verdict == ResponseVerdict.CHALLENGE:
            heuristic_result.analysis_time_ms = (time.monotonic() - start) * 1000
            self._challenge_count += 1
            logger.warning(f"ResponseRouter: CHALLENGE detected (heuristic)")
            return heuristic_result

        if heuristic_result.verdict == ResponseVerdict.SUSPICIOUS:
            heuristic_result.analysis_time_ms = (time.monotonic() - start) * 1000
            logger.warning(f"ResponseRouter: SUSPICIOUS content (heuristic)")
            return heuristic_result

        # LLM analysis for uncertain cases (only if text is substantial)
        if use_llm and self._llm and len(text) > 50:
            llm_result = await self._llm_inspect(text)
            if llm_result:
                llm_result.analysis_time_ms = (time.monotonic() - start) * 1000
                if llm_result.verdict == ResponseVerdict.CHALLENGE:
                    self._challenge_count += 1
                return llm_result

        # Default: normal
        return RouterResult(
            verdict=ResponseVerdict.NORMAL,
            analysis_time_ms=(time.monotonic() - start) * 1000,
        )

    def inspect_sync(self, data: Any) -> RouterResult:
        """
        Synchronous heuristic-only inspection.

        For use in contexts where async is not available.
        """
        text = self._to_text(data)
        return self._heuristic_check(text)

    def _heuristic_check(self, text: str) -> RouterResult:
        """Fast pattern-based check."""
        # Check for challenges
        for pattern in _CHALLENGE_PATTERNS:
            if pattern.search(text):
                return RouterResult(
                    verdict=ResponseVerdict.CHALLENGE,
                    confidence=0.9,
                    details={"matched_pattern": pattern.pattern},
                )

        # Check for suspicious content
        for pattern in _SUSPICIOUS_PATTERNS:
            if pattern.search(text):
                return RouterResult(
                    verdict=ResponseVerdict.SUSPICIOUS,
                    confidence=0.7,
                    details={"matched_pattern": pattern.pattern},
                )

        return RouterResult(verdict=ResponseVerdict.NORMAL)

    async def _llm_inspect(self, text: str) -> Optional[RouterResult]:
        """LLM-based inspection."""
        try:
            # Truncate for speed
            truncated = text[:2000] if len(text) > 2000 else text

            prompt = _INSPECT_PROMPT.format(data=truncated)
            response = await self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=150,
            )

            if not response:
                return None

            content = response.get("content", "")
            try:
                result = json.loads(content.strip())
            except json.JSONDecodeError:
                json_match = re.search(r"\{[^}]+\}", content)
                if json_match:
                    result = json.loads(json_match.group())
                else:
                    return None

            verdict_str = result.get("verdict", "NORMAL").upper()
            verdict_map = {
                "NORMAL": ResponseVerdict.NORMAL,
                "CHALLENGE": ResponseVerdict.CHALLENGE,
                "SUSPICIOUS": ResponseVerdict.SUSPICIOUS,
            }

            return RouterResult(
                verdict=verdict_map.get(verdict_str, ResponseVerdict.NORMAL),
                confidence=float(result.get("confidence", 0.5)),
                details={"reason": result.get("reason", "")},
            )

        except Exception as e:
            logger.error(f"ResponseRouter: LLM inspection failed: {e}")
            return None

    @staticmethod
    def _to_text(data: Any) -> str:
        """Convert response data to searchable text."""
        if isinstance(data, str):
            return data
        if isinstance(data, dict):
            return json.dumps(data, default=str)
        if isinstance(data, (list, tuple)):
            return json.dumps(data, default=str)
        return str(data)

    def get_stats(self) -> dict:
        return {
            "inspections": self._inspection_count,
            "challenges_detected": self._challenge_count,
        }
