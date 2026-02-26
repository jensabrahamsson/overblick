"""
Reply generator for the email agent.

Owns reply composition (via LLM), tone consultation (via personality consultant),
research requests (via boss capability), and draft reply notifications.
"""

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from overblick.core.security.input_sanitizer import wrap_external_content
from overblick.plugins.email_agent.models import SenderProfile
from overblick.plugins.email_agent.prompts import (
    reply_prompt,
    reply_prompt_with_research,
    tone_consultation_prompt,
)

if TYPE_CHECKING:
    from overblick.core.plugin_base import PluginContext
    from overblick.plugins.email_agent.database import EmailAgentDB
    from overblick.plugins.email_agent.reputation import ReputationManager

logger = logging.getLogger(__name__)


class ReplyGenerator:
    """
    Generates and sends email replies using the LLM pipeline.

    Supports tone consultation (via personality_consultant capability),
    research lookups (via boss_request capability), and draft notifications
    (via telegram_notifier capability).
    """

    def __init__(
        self,
        ctx: "PluginContext",
        principal_name: str,
        db: Optional["EmailAgentDB"],
        reputation: "ReputationManager",
    ) -> None:
        self._ctx = ctx
        self._principal_name = principal_name
        self._db = db
        self._reputation = reputation

    async def generate_and_send(
        self, email: dict[str, Any], research_context: str = "",
    ) -> bool:
        """Generate and send an email reply via the Gmail plugin event bus."""
        sender = email.get("sender", "")
        subject = email.get("subject", "")
        body = email.get("body", "")

        # Get sender context from profile (GDPR-safe) and DB history
        profile = await self._reputation.load_sender_profile(sender)
        sender_context = "No previous interactions"
        interaction_history = "First contact"

        if profile.total_interactions > 0:
            sender_context = (
                f"Total interactions: {profile.total_interactions}. "
                f"Preferred language: {profile.preferred_language or 'unknown'}. "
                f"Avg confidence: {profile.avg_confidence:.2f}. "
                f"Last contact: {profile.last_interaction_date}."
            )
            if profile.notes:
                sender_context += f" Notes: {profile.notes}"

        if self._db:
            history = await self._db.get_sender_history(sender, limit=5)
            if history:
                interaction_history = "\n".join(
                    f"- {r.email_subject}: {r.classified_intent}"
                    for r in history[:3]
                )

        # Consult Cherry for tone advice before generating the reply
        tone_guidance = await self._consult_tone(sender, subject, body, sender_context)
        if tone_guidance:
            research_context = (
                f"{research_context}\n\n{tone_guidance}" if research_context
                else tone_guidance
            )

        safe_sender = wrap_external_content(sender, "email_sender")
        safe_subject = wrap_external_content(subject, "email_subject")
        safe_body = wrap_external_content(body[:3000], "email_body")
        if research_context:
            messages = reply_prompt_with_research(
                sender=safe_sender,
                subject=safe_subject,
                body=safe_body,
                sender_context=sender_context,
                interaction_history=interaction_history,
                principal_name=self._principal_name,
                research_context=research_context,
            )
        else:
            messages = reply_prompt(
                sender=safe_sender,
                subject=safe_subject,
                body=safe_body,
                sender_context=sender_context,
                interaction_history=interaction_history,
                principal_name=self._principal_name,
            )

        try:
            result = await self._ctx.llm_pipeline.chat(
                messages=messages,
                audit_action="email_reply",
                complexity="high",
            )
            if not result or result.blocked or not result.content:
                return False

            gmail_cap = self._ctx.get_capability("gmail")
            if gmail_cap:
                reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"
                thread_id = email.get("thread_id", "")
                msg_id = email.get("message_id", "")
                success = await gmail_cap.send_reply(
                    thread_id=thread_id,
                    message_id=msg_id,
                    to=sender,
                    subject=reply_subject,
                    body=result.content.strip(),
                )
                return success
            else:
                logger.warning("EmailAgent: gmail capability not available for reply")
                return False

        except Exception as e:
            logger.error("EmailAgent: reply generation failed: %s", e, exc_info=True)
            return False

    async def send_draft_notification(
        self, email: dict[str, Any], notifier: Any,
    ) -> Optional[tuple[int, str]]:
        """
        Generate a draft reply and send it as a tracked Telegram message.

        Lets the principal see how Stål would respond, building trust before
        automatic replies are activated. Returns (tg_message_id, draft_body)
        so the caller can track it in the database for approve-to-send.

        Always best-effort — failures are logged as warnings, never propagated.
        """
        if not notifier:
            return None

        sender = email.get("sender", "")
        subject = email.get("subject", "")
        body = email.get("body", "")

        # Load real sender context (same as generate_and_send)
        profile = await self._reputation.load_sender_profile(sender)
        sender_context = "No previous interactions"
        interaction_history = "First contact"

        if profile.total_interactions > 0:
            sender_context = (
                f"Total interactions: {profile.total_interactions}. "
                f"Preferred language: {profile.preferred_language or 'unknown'}. "
                f"Avg confidence: {profile.avg_confidence:.2f}. "
                f"Last contact: {profile.last_interaction_date}."
            )
            if profile.notes:
                sender_context += f" Notes: {profile.notes}"

        if self._db:
            history = await self._db.get_sender_history(sender, limit=5)
            if history:
                interaction_history = "\n".join(
                    f"- {r.email_subject}: {r.classified_intent}"
                    for r in history[:3]
                )

        # Consult for tone advice
        tone_guidance = await self._consult_tone(sender, subject, body, sender_context)

        safe_sender = wrap_external_content(sender, "email_sender")
        safe_subject = wrap_external_content(subject, "email_subject")
        safe_body = wrap_external_content(body[:3000], "email_body")

        if tone_guidance:
            messages = reply_prompt_with_research(
                sender=safe_sender,
                subject=safe_subject,
                body=safe_body,
                sender_context=sender_context,
                interaction_history=interaction_history,
                principal_name=self._principal_name,
                research_context=tone_guidance,
            )
        else:
            messages = reply_prompt(
                sender=safe_sender,
                subject=safe_subject,
                body=safe_body,
                sender_context=sender_context,
                interaction_history=interaction_history,
                principal_name=self._principal_name,
            )

        try:
            result = await self._ctx.llm_pipeline.chat(
                messages=messages,
                audit_action="email_draft_reply",
                skip_preflight=True,
                complexity="high",
            )
            if not result or result.blocked or not result.content:
                logger.warning(
                    "EmailAgent: draft reply blocked or empty for email from %s", sender,
                )
                return None

            draft_body = result.content.strip()
            reply_subject = subject if subject.startswith("Re:") else f"Re: {subject}"
            draft_text = (
                f"\u270f\ufe0f *Draft reply to {sender}:*\n"
                f"{reply_subject}\n\n"
                f"{draft_body}\n\n"
                f'_Reply "skicka" to send this reply._'
            )
            tg_message_id = await notifier.send_notification_tracked(draft_text)

            if tg_message_id:
                return (tg_message_id, draft_body)
            return None

        except Exception as e:
            logger.warning(
                "EmailAgent: draft reply notification failed for email from %s: %s",
                sender, e,
            )
            return None

    async def _consult_tone(
        self,
        sender: str,
        subject: str,
        body: str,
        sender_context: str,
    ) -> Optional[str]:
        """
        Consult a personality (default: Cherry) for tone advice.

        Returns tone guidance string to inject into the reply prompt,
        or None if the capability is unavailable or consultation fails.
        """
        consultant = self._ctx.get_capability("personality_consultant")
        if not consultant:
            return None

        query = tone_consultation_prompt(
            sender=sender,
            subject=subject,
            body=body[:2000],
            sender_context=sender_context,
        )

        try:
            raw = await consultant.consult(query=query)
            if not raw:
                return None

            advice = json.loads(
                raw[raw.find("{"):raw.rfind("}") + 1] if "{" in raw else raw,
            )
            tone = advice.get("tone", "professional")
            guidance = advice.get("guidance", "")

            logger.info(
                "EmailAgent: tone consultation result — tone=%s for email from %s",
                tone, sender,
            )

            if tone == "warm" and guidance:
                return f"TONE GUIDANCE (from personality consultation): {guidance}"

            return None

        except (json.JSONDecodeError, Exception) as e:
            logger.debug("EmailAgent: tone consultation parse error: %s", e)
            return None

    async def _request_research(self, query: str, context: str = "") -> Optional[str]:
        """Ask the supervisor for research via BossRequestCapability."""
        boss_cap = self._ctx.get_capability("boss_request")
        if not boss_cap or not boss_cap.configured:
            logger.debug("EmailAgent: boss_request capability not available for research")
            return None
        return await boss_cap.request_research(query, context)
