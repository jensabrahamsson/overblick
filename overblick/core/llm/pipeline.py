"""
Safe LLM Pipeline — single secure interface for all LLM interactions.

Consolidates the full security chain into one pipeline:
    Input sanitize → Preflight check → Rate limit → LLM call → Output safety → Audit

Plugins should use this instead of calling llm_client.chat() directly.
This guarantees that every LLM interaction passes through all security layers,
eliminating the risk of skipped checks.

Usage:
    pipeline = SafeLLMPipeline(llm_client=client, audit_log=audit, ...)
    result = await pipeline.chat(messages=[{"role": "user", "content": "Hello"}])
    if result.blocked:
        print(f"Blocked: {result.block_reason}")
    else:
        print(result.content)
"""

import logging
import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel

from overblick.core.security.input_sanitizer import sanitize as sanitize_input

logger = logging.getLogger(__name__)


class PipelineStage(Enum):
    """Which stage of the pipeline blocked or produced the result."""
    INPUT_SANITIZE = "input_sanitize"
    PREFLIGHT = "preflight"
    RATE_LIMIT = "rate_limit"
    LLM_CALL = "llm_call"
    OUTPUT_SAFETY = "output_safety"
    COMPLETE = "complete"


class PipelineResult(BaseModel):
    """
    Result of a pipeline execution.

    Contains either the final safe content or information about
    why the request was blocked.
    """
    content: Optional[str] = None
    blocked: bool = False
    block_reason: Optional[str] = None
    block_stage: Optional[PipelineStage] = None
    raw_response: Optional[dict] = None
    duration_ms: float = 0.0
    stages_passed: list[PipelineStage] = []

    # Deflection text to send back when blocked
    deflection: Optional[str] = None


class SafeLLMPipeline:
    """
    Single secure interface for all LLM interactions.

    Wraps an LLMClient with the full security chain. All optional
    components degrade gracefully — if a security module is None,
    that stage is skipped (but logged as a warning on first use).

    Pipeline stages:
        1. Input sanitize — Clean user/external text
        2. Preflight check — Block jailbreak/injection attempts
        3. Rate limit — Token bucket throttling
        4. LLM call — Actual model invocation
        5. Output safety — Filter AI language leakage
        6. Audit — Log the interaction
    """

    def __init__(
        self,
        llm_client: Any,
        audit_log: Any = None,
        preflight_checker: Any = None,
        output_safety: Any = None,
        rate_limiter: Any = None,
        identity_name: str = "",
        rate_limit_key: str = "llm_pipeline",
    ):
        self._llm = llm_client
        self._audit = audit_log
        self._preflight = preflight_checker
        self._output_safety = output_safety
        self._rate_limiter = rate_limiter
        self._identity_name = identity_name
        self._rate_limit_key = rate_limit_key

        # Track missing components (warn once)
        self._warned: set[str] = set()

    async def chat(
        self,
        messages: list[dict[str, str]],
        user_id: str = "system",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        sanitize_messages: bool = True,
        skip_preflight: bool = False,
        skip_output_safety: bool = False,
        audit_action: str = "llm_chat",
        audit_details: Optional[dict[str, Any]] = None,
        priority: str = "low",
    ) -> PipelineResult:
        """
        Send a chat request through the full security pipeline.

        Args:
            messages: Chat messages (role + content dicts)
            user_id: User ID for preflight tracking (default "system")
            temperature: Override LLM temperature
            max_tokens: Override max tokens
            top_p: Override top_p
            sanitize_messages: Whether to sanitize message content
            skip_preflight: Skip preflight check (for system-generated content)
            skip_output_safety: Skip output safety (for internal queries)
            audit_action: Action name for audit log
            audit_details: Extra audit details
            priority: Request priority ("high" or "low") for gateway queue ordering

        Returns:
            PipelineResult with safe content or block information
        """
        start = time.monotonic()
        stages: list[PipelineStage] = []

        # Stage 1: Input sanitize
        if sanitize_messages:
            messages = self._sanitize_messages(messages)
        stages.append(PipelineStage.INPUT_SANITIZE)

        # Stage 2: Preflight check (on last user message)
        if not skip_preflight:
            result = await self._run_preflight(messages, user_id)
            if result:
                result.duration_ms = (time.monotonic() - start) * 1000
                result.stages_passed = stages
                self._audit_blocked(result, audit_action, audit_details)
                return result
        else:
            self._audit_skip("preflight", user_id, audit_action)
        stages.append(PipelineStage.PREFLIGHT)

        # Stage 3: Rate limit
        if self._rate_limiter:
            if not self._rate_limiter.allow(self._rate_limit_key):
                wait = self._rate_limiter.retry_after(self._rate_limit_key)
                result = PipelineResult(
                    blocked=True,
                    block_reason=f"Rate limited, retry after {wait:.1f}s",
                    block_stage=PipelineStage.RATE_LIMIT,
                    duration_ms=(time.monotonic() - start) * 1000,
                    stages_passed=stages,
                )
                self._audit_blocked(result, audit_action, audit_details)
                return result
        else:
            self._warn_missing("rate_limiter")
        stages.append(PipelineStage.RATE_LIMIT)

        # Stage 4: LLM call
        try:
            raw_response = await self._llm.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                priority=priority,
            )
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            result = PipelineResult(
                blocked=True,
                block_reason=f"LLM error: {e}",
                block_stage=PipelineStage.LLM_CALL,
                duration_ms=(time.monotonic() - start) * 1000,
                stages_passed=stages,
            )
            self._audit_error(result, audit_action, audit_details, str(e))
            return result

        if not raw_response:
            result = PipelineResult(
                blocked=True,
                block_reason="LLM returned empty response",
                block_stage=PipelineStage.LLM_CALL,
                duration_ms=(time.monotonic() - start) * 1000,
                stages_passed=stages,
            )
            self._audit_error(result, audit_action, audit_details, "empty_response")
            return result

        content = raw_response.get("content", "")
        stages.append(PipelineStage.LLM_CALL)

        # Stage 5: Output safety
        if not skip_output_safety:
            safety_result = self._run_output_safety(content)
            if safety_result is not None:
                # Output was blocked or modified
                blocked, safe_text, reason = safety_result
                if blocked:
                    result = PipelineResult(
                        blocked=True,
                        block_reason=reason,
                        block_stage=PipelineStage.OUTPUT_SAFETY,
                        deflection=safe_text,
                        raw_response=raw_response,
                        duration_ms=(time.monotonic() - start) * 1000,
                        stages_passed=stages,
                    )
                    self._audit_blocked(result, audit_action, audit_details)
                    return result
                else:
                    # Content was modified (e.g. slang replaced)
                    content = safe_text
        else:
            self._audit_skip("output_safety", user_id, audit_action)
        stages.append(PipelineStage.OUTPUT_SAFETY)
        stages.append(PipelineStage.COMPLETE)

        # Stage 6: Audit success
        duration = (time.monotonic() - start) * 1000
        result = PipelineResult(
            content=content,
            raw_response=raw_response,
            duration_ms=duration,
            stages_passed=stages,
        )

        if self._audit:
            details = audit_details or {}
            details["duration_ms"] = duration
            details["content_length"] = len(content)
            self._audit.log(
                action=audit_action,
                category="llm",
                details=details,
                success=True,
                duration_ms=duration,
            )

        return result

    def _sanitize_messages(
        self, messages: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """Sanitize all message content."""
        sanitized = []
        for msg in messages:
            sanitized.append({
                "role": msg.get("role", "user"),
                "content": sanitize_input(msg.get("content", "")),
            })
        return sanitized

    async def _run_preflight(
        self,
        messages: list[dict[str, str]],
        user_id: str,
    ) -> Optional[PipelineResult]:
        """Run preflight check on last user message. Returns PipelineResult if blocked."""
        if not self._preflight:
            self._warn_missing("preflight_checker")
            return None

        # Find last user message
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content", "")
                break

        if not last_user_msg:
            return None

        try:
            check = await self._preflight.check(last_user_msg, user_id)
            if not check.allowed:
                return PipelineResult(
                    blocked=True,
                    block_reason=check.reason or "Preflight blocked",
                    block_stage=PipelineStage.PREFLIGHT,
                    deflection=check.deflection,
                )
        except Exception as e:
            logger.error("Preflight check error: %s", e)
            # Fail CLOSED — block if preflight crashes (security-first)
            return PipelineResult(
                blocked=True,
                block_reason=f"Preflight check unavailable: {e}",
                block_stage=PipelineStage.PREFLIGHT,
            )
        return None

    def _run_output_safety(
        self, content: str
    ) -> Optional[tuple[bool, str, Optional[str]]]:
        """Run output safety check. Returns (blocked, text, reason) or None."""
        if not self._output_safety:
            self._warn_missing("output_safety")
            return None

        try:
            result = self._output_safety.sanitize(content)
            if result.blocked:
                return (True, result.text, result.reason)
            if result.replaced:
                return (False, result.text, None)
        except Exception as e:
            logger.error("Output safety error: %s", e)
            # Fail CLOSED — block if output safety crashes (security-first)
            return (True, "", f"Output safety unavailable: {e}")

        return None

    def _audit_blocked(
        self,
        result: PipelineResult,
        action: str,
        details: Optional[dict[str, Any]],
    ) -> None:
        """Log a blocked request to audit."""
        if not self._audit:
            return
        d = details or {}
        d["block_stage"] = result.block_stage.value if result.block_stage else None
        d["block_reason"] = result.block_reason
        self._audit.log(
            action=f"{action}_blocked",
            category="security",
            details=d,
            success=False,
        )

    def _audit_error(
        self,
        result: PipelineResult,
        action: str,
        details: Optional[dict[str, Any]],
        error: str,
    ) -> None:
        """Log an error to audit."""
        if not self._audit:
            return
        d = details or {}
        d["stage"] = result.block_stage.value if result.block_stage else None
        self._audit.log(
            action=f"{action}_error",
            category="llm",
            details=d,
            success=False,
            error=error,
        )

    def _audit_skip(self, stage: str, user_id: str, action: str) -> None:
        """Audit when a security stage is explicitly skipped."""
        if self._audit:
            self._audit.log(
                action=f"{stage}_skipped",
                category="security",
                details={"caller": user_id, "audit_action": action},
            )

    def _warn_missing(self, component: str) -> None:
        """Warn about missing optional component (once per component)."""
        if component not in self._warned:
            logger.warning(
                "SafeLLMPipeline: %s not configured — stage skipped", component
            )
            self._warned.add(component)
