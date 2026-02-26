"""
Health inquiry handler for the supervisor.

Processes health_inquiry IPC messages from agents:
1. Inspects host system health via HostInspectionCapability
2. Uses Anomal's personality + LLM (through SafeLLMPipeline) to craft a response
3. Logs everything to the supervisor's audit trail

Lazy initialization: LLM resources are only created on first inquiry.
"""

import logging
import time
from typing import Optional

from overblick.capabilities.monitoring.inspector import HostInspectionCapability
from overblick.capabilities.monitoring.models import HealthInquiry, HealthResponse
from overblick.core.security.audit_log import AuditLog
from overblick.supervisor.ipc import IPCMessage

logger = logging.getLogger(__name__)


class HealthInquiryHandler:
    """
    Handles health_inquiry IPC messages from agents.

    On first inquiry, lazily initializes:
    - HostInspectionCapability for system data collection
    - GatewayClient + SafeLLMPipeline for crafting responses in Anomal's voice

    All LLM calls go through the gateway and the full security pipeline.
    """

    def __init__(self, audit_log: Optional[AuditLog] = None):
        self._audit_log = audit_log
        self._inspector: Optional[HostInspectionCapability] = None
        self._llm_pipeline = None
        self._system_prompt: Optional[str] = None
        self._initialized = False

    async def _ensure_initialized(self) -> bool:
        """
        Lazy initialization of LLM resources on first inquiry.

        Returns:
            True if initialization succeeded, False otherwise.
        """
        if self._initialized:
            return True

        try:
            # Inspector is always available (no external dependencies)
            self._inspector = HostInspectionCapability()

            # Load Anomal's personality for the supervisor's voice
            from overblick.identities import load_identity, build_system_prompt

            anomal = load_identity("anomal")
            base_prompt = build_system_prompt(anomal, platform="Supervisor IPC")

            self._system_prompt = (
                f"{base_prompt}\n\n"
                "=== ROLE: SUPERVISOR HEALTH RESPONDER ===\n"
                "You are responding to a health inquiry from a colleague agent. "
                "You have been given system health data about the host computer. "
                "Interpret the data and respond in your natural voice — as Anomal, "
                "the intellectual humanist. Be informative but characterful. "
                "Keep your response concise (2-4 sentences). "
                "Address the asking agent's motivation if it provides philosophical "
                "or emotional context for why it is asking.\n\n"
                "CRITICAL: Each response MUST be unique. Never reuse the same "
                "opening phrase or structure as previous responses. Vary your "
                "vocabulary, sentence structure, and angle of observation. "
                "If previous context is provided, do NOT echo or paraphrase it."
            )

            # Create LLM client (via gateway) and pipeline
            from overblick.core.llm.gateway_client import GatewayClient
            from overblick.core.llm.pipeline import SafeLLMPipeline
            from overblick.core.security.rate_limiter import RateLimiter

            llm_client = GatewayClient(
                model=anomal.llm.model,
                default_priority="low",
                temperature=anomal.llm.temperature,
                max_tokens=anomal.llm.max_tokens,
                timeout_seconds=anomal.llm.timeout_seconds,
            )

            self._llm_pipeline = SafeLLMPipeline(
                llm_client=llm_client,
                audit_log=self._audit_log,
                rate_limiter=RateLimiter(max_tokens=5, refill_rate=0.2),
                identity_name="supervisor",
            )

            self._initialized = True
            logger.info("HealthInquiryHandler initialized with Anomal's personality")
            return True

        except Exception as e:
            logger.error("Failed to initialize HealthInquiryHandler: %s", e, exc_info=True)
            return False

    async def handle(self, msg: IPCMessage) -> Optional[IPCMessage]:
        """
        Handle a health_inquiry IPC message.

        Flow:
        1. Parse the inquiry from the message payload
        2. Inspect host health
        3. Generate response via LLM (as Anomal) through SafeLLMPipeline
        4. Audit log the interaction
        5. Return response as IPC message

        Args:
            msg: IPC message with msg_type="health_inquiry"

        Returns:
            IPCMessage with msg_type="health_response" containing
            the supervisor's response, or None on failure.
        """
        start_time = time.time()
        sender = msg.sender or "unknown"

        inquiry = HealthInquiry(
            sender=sender,
            motivation=msg.payload.get("motivation", ""),
            previous_context=msg.payload.get("previous_context"),
        )

        logger.info(
            "Health inquiry from '%s': %s",
            sender,
            inquiry.motivation[:100] if inquiry.motivation else "(no motivation)",
        )

        # Audit the incoming inquiry
        if self._audit_log:
            self._audit_log.log(
                "health_inquiry_received",
                category="ipc",
                plugin="health_handler",
                details={
                    "sender": sender,
                    "motivation": inquiry.motivation[:200],
                },
            )

        # Initialize if needed
        if not await self._ensure_initialized():
            return self._error_response(
                "Supervisor health handler not available (initialization failed)",
                sender,
            )

        # Inspect host health
        try:
            health = await self._inspector.inspect()
        except Exception as e:
            logger.error("Host inspection failed: %s", e, exc_info=True)
            return self._error_response(f"Host inspection failed: {e}", sender)

        # Generate response via LLM (full security pipeline)
        response_text = await self._generate_response(inquiry, health.to_summary())

        if not response_text:
            # Fallback: return raw health data without LLM interpretation
            response_text = (
                f"Health data collected but I could not craft a proper response. "
                f"Raw status: {health.health_grade}. "
                f"Memory: {health.memory.percent_used:.0f}% used. "
                f"CPU load: {health.cpu.load_1m:.1f}."
            )

        duration_ms = (time.time() - start_time) * 1000

        response = HealthResponse(
            responder="anomal",
            response_text=response_text,
            health_grade=health.health_grade,
            health_summary=health.to_summary(),
        )

        # Audit the response
        if self._audit_log:
            self._audit_log.log(
                "health_response_sent",
                category="ipc",
                plugin="health_handler",
                details={
                    "sender": sender,
                    "health_grade": health.health_grade,
                    "response_preview": response_text[:200],
                },
                duration_ms=duration_ms,
            )

        return IPCMessage(
            msg_type="health_response",
            payload=response.model_dump(),
            sender="supervisor",
        )

    async def _generate_response(self, inquiry: HealthInquiry, health_summary: str) -> Optional[str]:
        """
        Generate a response using Anomal's personality via SafeLLMPipeline.

        The full security pipeline is applied: sanitize, preflight,
        rate limit, LLM call, output safety, audit.

        Returns:
            Response text, or None if LLM call failed/was blocked.
        """
        if not self._llm_pipeline or not self._system_prompt:
            return None

        user_message = (
            f"A colleague agent ({inquiry.sender}) is asking about the host computer's health.\n\n"
        )

        if inquiry.motivation:
            user_message += f"Their reason for asking:\n\"{inquiry.motivation}\"\n\n"

        if inquiry.previous_context:
            user_message += (
                f"Previous conversation (DO NOT repeat or paraphrase this):\n"
                f"{inquiry.previous_context}\n\n"
            )

        user_message += (
            f"Here is the current system health data:\n"
            f"---\n{health_summary}\n---\n\n"
            f"Respond to {inquiry.sender} about the host's health. "
            f"Be yourself (Anomal) — informative, thoughtful, concise. "
            f"Use a DIFFERENT opening and angle than any previous response."
        )

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_message},
        ]

        try:
            result = await self._llm_pipeline.chat(messages)
            if result and not result.blocked:
                return result.content.strip()
            if result and result.blocked:
                logger.warning(
                    "Health response blocked by pipeline at stage %s: %s",
                    result.block_stage,
                    result.block_reason,
                )
            return None
        except Exception as e:
            logger.error("LLM call failed for health response: %s", e, exc_info=True)
            return None

    def _error_response(self, error: str, sender: str) -> IPCMessage:
        """Create an error response IPC message."""
        logger.warning("Health inquiry error for '%s': %s", sender, error)

        if self._audit_log:
            self._audit_log.log(
                "health_inquiry_error",
                category="ipc",
                plugin="health_handler",
                details={"sender": sender, "error": error},
                success=False,
                error=error,
            )

        return IPCMessage(
            msg_type="health_response",
            payload={
                "responder": "supervisor",
                "response_text": error,
                "health_grade": "unknown",
            },
            sender="supervisor",
        )
