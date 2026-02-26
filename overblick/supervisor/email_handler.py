"""
Email consultation handler for the supervisor.

Processes email_consultation IPC messages from the email agent (Stål).
Uses the supervisor's personality (Anomal) to provide guidance on
how to handle uncertain emails.

Lazy initialization: LLM resources are only created on first consultation.
"""

import logging
import time
from typing import Optional

from overblick.core.security.audit_log import AuditLog
from overblick.supervisor.ipc import IPCMessage

logger = logging.getLogger(__name__)


class EmailConsultationHandler:
    """
    Handles email_consultation IPC messages from agents.

    When an email agent is uncertain about how to classify an email
    (confidence below threshold), it asks the supervisor for guidance.
    The supervisor uses Anomal's personality to reason about the email
    and provide an advised action.
    """

    def __init__(self, audit_log: Optional[AuditLog] = None):
        self._audit_log = audit_log
        self._llm_pipeline = None
        self._system_prompt: Optional[str] = None
        self._initialized = False

    async def _ensure_initialized(self) -> bool:
        """Lazy initialization of LLM resources on first consultation."""
        if self._initialized:
            return True

        try:
            from overblick.identities import load_identity, build_system_prompt

            anomal = load_identity("anomal")
            base_prompt = build_system_prompt(anomal, platform="Supervisor IPC")

            self._system_prompt = (
                f"{base_prompt}\n\n"
                "=== ROLE: EMAIL CONSULTATION ADVISOR ===\n"
                "An agent (Stål, the email secretary) is asking for guidance on how "
                "to handle an email. Review the email context and advise which action "
                "to take:\n"
                "- ignore: Not relevant, spam, or automated\n"
                "- notify: Important, but the principal should see it personally\n"
                "- reply: Write a professional reply on the principal's behalf\n"
                "- ask_boss: Still unclear, escalate further\n\n"
                "IMPORTANT: Always respond in English. Internal agent communication "
                "uses English.\n\n"
                "Respond in JSON: {\"advised_action\": \"...\", \"reasoning\": \"...\"}"
            )

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
            logger.info("EmailConsultationHandler initialized with Anomal's personality")
            return True

        except Exception as e:
            logger.error("Failed to initialize EmailConsultationHandler: %s", e, exc_info=True)
            return False

    async def handle(self, msg: IPCMessage) -> Optional[IPCMessage]:
        """
        Handle an email_consultation IPC message.

        Args:
            msg: IPC message with msg_type="email_consultation"

        Returns:
            IPCMessage with advised action, or None on failure.
        """
        start_time = time.time()
        sender = msg.sender or "unknown"

        question = msg.payload.get("question", "")
        email_from = msg.payload.get("email_from", "")
        email_subject = msg.payload.get("email_subject", "")
        tentative_intent = msg.payload.get("tentative_intent", "")
        confidence = msg.payload.get("confidence", 0.0)

        logger.info(
            "Email consultation from '%s': %s (from=%s, subject=%s)",
            sender, question[:100], email_from, email_subject[:50],
        )

        if self._audit_log:
            self._audit_log.log(
                "email_consultation_received",
                category="ipc",
                plugin="email_handler",
                details={
                    "sender": sender,
                    "email_from": email_from,
                    "email_subject": email_subject[:100],
                    "tentative_intent": tentative_intent,
                },
            )

        if not await self._ensure_initialized():
            return self._fallback_response(sender, tentative_intent)

        # Generate advice via LLM
        advised_action, reasoning = await self._generate_advice(
            question, email_from, email_subject, tentative_intent, confidence,
        )

        duration_ms = (time.time() - start_time) * 1000

        if self._audit_log:
            self._audit_log.log(
                "email_consultation_response",
                category="ipc",
                plugin="email_handler",
                details={
                    "sender": sender,
                    "advised_action": advised_action,
                    "reasoning": reasoning[:200],
                },
                duration_ms=duration_ms,
            )

        return IPCMessage(
            msg_type="email_consultation_response",
            payload={
                "advised_action": advised_action,
                "reasoning": reasoning,
            },
            sender="supervisor",
        )

    async def _generate_advice(
        self,
        question: str,
        email_from: str,
        email_subject: str,
        tentative_intent: str,
        confidence: float,
    ) -> tuple[str, str]:
        """Generate advice using the supervisor's LLM personality."""
        if not self._llm_pipeline or not self._system_prompt:
            return tentative_intent or "notify", "LLM unavailable, defaulting to notification"

        user_message = (
            f"The email agent is asking for guidance:\n\n"
            f"Question: {question}\n\n"
            f"Email from: {email_from}\n"
            f"Subject: {email_subject}\n"
            f"Agent's tentative action: {tentative_intent}\n"
            f"Agent's confidence: {confidence:.2f}\n\n"
            "What action should the agent take?"
        )

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_message},
        ]

        try:
            result = await self._llm_pipeline.chat(messages)
            if result and not result.blocked and result.content:
                return self._parse_advice(result.content, tentative_intent)
        except Exception as e:
            logger.error("Email consultation LLM call failed: %s", e, exc_info=True)

        return tentative_intent or "notify", "LLM call failed, using agent's tentative intent"

    def _parse_advice(self, raw: str, fallback_action: str) -> tuple[str, str]:
        """Parse LLM JSON advice response."""
        import json

        try:
            data = json.loads(raw)
            action = data.get("advised_action", fallback_action)
            reasoning = data.get("reasoning", "")
            return action, reasoning
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start:end])
                    return data.get("advised_action", fallback_action), data.get("reasoning", "")
                except json.JSONDecodeError:
                    pass

        # Could not parse JSON — extract action from text
        for action in ("ignore", "notify", "reply", "ask_boss"):
            if action in raw.lower():
                return action, raw[:200]

        return fallback_action, raw[:200]

    def _fallback_response(self, sender: str, tentative_intent: str) -> IPCMessage:
        """Create a fallback response when LLM is not available."""
        return IPCMessage(
            msg_type="email_consultation_response",
            payload={
                "advised_action": tentative_intent or "notify",
                "reasoning": "Supervisor LLM unavailable, defaulting to safe action",
            },
            sender="supervisor",
        )
