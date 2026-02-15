"""
Agentic email plugin — prompt-driven decision making.

Unlike the classic GmailPlugin (hard-coded if/else), this plugin uses LLM
to classify emails and decide actions. It has goals, state, learnings,
and reinforcement via boss agent feedback.

Tick cycle:
1. Fetch unread emails (via Gmail plugin's event bus)
2. For each email, run classification prompt
3. Execute action based on LLM intent:
   - IGNORE: Log and skip
   - NOTIFY: Generate notification, send via Telegram
   - REPLY: Generate reply, send via Gmail plugin event bus
   - ASK_BOSS: Send IPC to supervisor, await guidance
4. Record classification + outcome in database
5. If boss provides feedback, update learnings
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.security.input_sanitizer import wrap_external_content
from overblick.plugins.email_agent.database import EmailAgentDB
from overblick.plugins.email_agent.models import (
    AgentGoal,
    AgentLearning,
    AgentState,
    EmailClassification,
    EmailIntent,
    EmailRecord,
    SenderProfile,
)
from overblick.plugins.email_agent.prompts import (
    boss_consultation_prompt,
    classification_prompt,
    feedback_classification_prompt,
    notification_prompt,
    reply_prompt,
    reply_prompt_with_research,
    tone_consultation_prompt,
)
from overblick.supervisor.ipc import IPCMessage

logger = logging.getLogger(__name__)

# Default goals for a new agent
_DEFAULT_GOALS = [
    AgentGoal(
        description="Classify all incoming emails accurately",
        priority=90,
    ),
    AgentGoal(
        description="Reply to emails in the sender's language",
        priority=85,
    ),
    AgentGoal(
        description="Learn from boss feedback to improve classification",
        priority=70,
    ),
]


class EmailAgentPlugin(PluginBase):
    """
    Agentic email plugin — prompt-driven email classification and action.

    Uses Stål's personality for reply generation. All decision-making
    flows through the LLM pipeline (SafeLLMPipeline).
    """

    name = "email_agent"

    # GDPR retention period (days) for email content in database
    GDPR_RETENTION_DAYS = 30

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._db: Optional[EmailAgentDB] = None
        self._state = AgentState()
        self._learnings: list[AgentLearning] = []
        self._system_prompt: str = ""
        self._check_interval: int = 300  # 5 minutes
        self._filter_mode: str = "opt_in"
        self._allowed_senders: set[str] = set()
        self._blocked_senders: set[str] = set()
        self._profiles_dir: Optional[Path] = None
        self._principal_name: str = ""

    async def setup(self) -> None:
        """Initialize the email agent: database, state, goals, prompt."""
        identity = self.ctx.identity
        if not identity:
            raise RuntimeError("EmailAgentPlugin requires an identity")

        # Initialize database
        db_path = self.ctx.data_dir / "email_agent.db"
        from overblick.core.database.base import DatabaseConfig
        from overblick.core.database.sqlite_backend import SQLiteBackend

        config = DatabaseConfig(sqlite_path=str(db_path))
        backend = SQLiteBackend(config)
        self._db = EmailAgentDB(backend)
        await self._db.setup()

        # Load state from DB
        stats = await self._db.get_stats()
        goals = await self._db.get_active_goals()
        self._state = AgentState(goals=goals, **stats)

        # Load learnings for prompt context
        self._learnings = await self._db.get_learnings(limit=50)

        # Initialize default goals if none exist
        if not self._state.goals:
            await self._initialize_default_goals()

        # Build system prompt from personality
        self._system_prompt = self._build_system_prompt()

        # Load email agent config from personality
        raw_config = identity.raw_config
        ea_config = raw_config.get("email_agent", {})
        self._filter_mode = ea_config.get("filter_mode", "opt_in")
        self._allowed_senders = set(ea_config.get("allowed_senders", []))
        self._blocked_senders = set(ea_config.get("blocked_senders", []))

        # Sender profiles directory (GDPR-safe consolidated data)
        self._profiles_dir = self.ctx.data_dir / "sender_profiles"
        self._profiles_dir.mkdir(parents=True, exist_ok=True)

        # Load principal name from secrets (injected at runtime — never hardcoded)
        self._principal_name = self.ctx.get_secret("principal_name") or ""

        # Run GDPR cleanup on startup
        await self._db.purge_gdpr_data(self.GDPR_RETENTION_DAYS)

        # Check interval from schedule
        self._check_interval = identity.schedule.feed_poll_minutes * 60

        logger.info(
            "EmailAgentPlugin setup for '%s' (filter: %s, learnings: %d, goals: %d)",
            self.ctx.identity_name,
            self._filter_mode,
            len(self._learnings),
            len(self._state.goals),
        )

    async def tick(self) -> None:
        """
        Main tick: check for unread emails and process them.

        Guards: interval, quiet hours, LLM pipeline availability.
        """
        now = time.time()

        # Guard: check interval
        if self._state.last_check and (now - self._state.last_check < self._check_interval):
            return

        # Guard: quiet hours
        if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
            return

        # Guard: LLM pipeline required
        if not self.ctx.llm_pipeline:
            logger.debug("EmailAgent: no LLM pipeline available")
            return

        self._state.last_check = now

        try:
            emails = await self._fetch_unread()
            for email in emails:
                await self._process_email(email)
            # Check for Telegram feedback after processing emails
            await self._check_tg_feedback()
        except Exception as e:
            logger.error("EmailAgent tick failed: %s", e, exc_info=True)
            self._state.current_health = "degraded"

    async def _fetch_unread(self) -> list[dict[str, Any]]:
        """
        Fetch unread emails via GmailCapability.

        Converts GmailMessage objects to dicts for the classification pipeline.
        """
        logger.debug("EmailAgent: checking for unread emails")

        gmail_cap = self.ctx.get_capability("gmail")
        if not gmail_cap:
            logger.debug("EmailAgent: gmail capability not available")
            return []

        messages = await gmail_cap.fetch_unread(max_results=10)

        return [
            {
                "message_id": msg.message_id,
                "thread_id": msg.thread_id,
                "sender": msg.sender,
                "subject": msg.subject,
                "body": msg.body,
                "snippet": msg.snippet,
            }
            for msg in messages
        ]

    async def _process_email(self, email: dict[str, Any]) -> None:
        """Process a single email through the classification pipeline."""
        sender = email.get("sender", "")
        subject = email.get("subject", "")
        body = email.get("body", "")
        snippet = email.get("snippet", body[:200])

        # Wrap external content in boundary markers
        safe_subject = wrap_external_content(subject, "email_subject")
        safe_body = wrap_external_content(body[:3000], "email_body")

        # Classify via LLM
        classification = await self._classify_email(sender, safe_subject, safe_body)
        if not classification:
            logger.warning("EmailAgent: classification failed for email from %s", sender)
            return

        # Confidence check — below threshold triggers ask_boss
        if classification.confidence < self._state.confidence_threshold:
            classification.intent = EmailIntent.ASK_BOSS
            logger.info(
                "EmailAgent: low confidence (%.2f) for %s — escalating to boss",
                classification.confidence, sender,
            )

        # Record in database FIRST to get record_id for notification tracking
        email_record_id = None
        if self._db:
            email_record_id = await self._db.record_email(EmailRecord(
                email_from=sender,
                email_subject=subject,
                email_snippet=snippet,
                classified_intent=classification.intent.value,
                confidence=classification.confidence,
                reasoning=classification.reasoning,
                action_taken="pending",
            ))

        # Execute action (pass record_id for notification tracking)
        action_taken = await self._execute_action(
            email, classification, email_record_id=email_record_id,
        )

        # Update action_taken in database
        if self._db and email_record_id:
            await self._db.update_action_taken(email_record_id, action_taken)

        self._state.emails_processed += 1

        # Update sender profile (GDPR-safe consolidated data)
        await self._update_sender_profile(sender, classification)

        logger.info(
            "EmailAgent: processed email from %s — %s (confidence: %.2f)",
            sender, classification.intent.value, classification.confidence,
        )

    async def _classify_email(
        self, sender: str, subject: str, body: str,
    ) -> Optional[EmailClassification]:
        """Run classification prompt — pure LLM decision."""
        # Build context strings
        goals_text = "\n".join(
            f"- {g.description} (priority: {g.priority})"
            for g in self._state.goals
        ) or "No active goals"

        learnings_text = "\n".join(
            f"- [{l.learning_type}] {l.content}"
            for l in self._learnings[:10]
        ) or "No learnings yet"

        # Get sender history from DB
        sender_history_text = "No previous interactions"
        if self._db:
            history = await self._db.get_sender_history(sender, limit=5)
            if history:
                sender_history_text = "\n".join(
                    f"- {r.email_subject}: {r.classified_intent} (confidence: {r.confidence:.2f})"
                    for r in history
                )

        messages = classification_prompt(
            goals=goals_text,
            learnings=learnings_text,
            sender_history=sender_history_text,
            sender=sender,
            subject=subject,
            body=body,
            principal_name=self._principal_name,
            allowed_senders=", ".join(self._allowed_senders),
        )

        try:
            result = await self.ctx.llm_pipeline.chat(
                messages=messages,
                audit_action="email_classification",
                skip_preflight=True,  # System-generated content
            )
            if result and not result.blocked and result.content:
                return self._parse_classification(result.content)
        except Exception as e:
            logger.error("EmailAgent: classification LLM call failed: %s", e)

        return None

    def _parse_classification(self, raw: str) -> Optional[EmailClassification]:
        """Parse LLM JSON output into EmailClassification."""
        # Extract JSON from response (may have surrounding text)
        try:
            # Try direct parse first
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Try extracting JSON from text
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start:end])
                except json.JSONDecodeError:
                    logger.warning("EmailAgent: could not parse classification JSON: %s", raw[:200])
                    return None
            else:
                logger.warning("EmailAgent: no JSON found in classification response")
                return None

        try:
            intent_str = data.get("intent", "ignore").lower()
            intent = EmailIntent(intent_str)
            return EmailClassification(
                intent=intent,
                confidence=float(data.get("confidence", 0.5)),
                reasoning=str(data.get("reasoning", "")),
                priority=str(data.get("priority", "normal")),
            )
        except (ValueError, KeyError) as e:
            logger.warning("EmailAgent: invalid classification data: %s", e)
            return None

    async def _execute_action(
        self,
        email: dict[str, Any],
        classification: EmailClassification,
        email_record_id: Optional[int] = None,
    ) -> str:
        """Execute the classified action. Returns description of action taken."""
        sender = email.get("sender", "")

        match classification.intent:
            case EmailIntent.IGNORE:
                return "ignored"

            case EmailIntent.NOTIFY:
                success = await self._send_notification(
                    email, classification, email_record_id=email_record_id,
                )
                if success:
                    self._state.notifications_sent += 1
                return "notification_sent" if success else "notification_failed"

            case EmailIntent.REPLY:
                if not self._is_allowed_sender(sender):
                    logger.info(
                        "Reply suppressed for sender %s — not in allowed list, "
                        "falling back to notification",
                        sender,
                    )
                    # Fall back to NOTIFY — important email, just can't reply
                    success = await self._send_notification(
                        email, classification, email_record_id=email_record_id,
                    )
                    if success:
                        self._state.notifications_sent += 1
                    return "reply_suppressed_notify_fallback" if success else "reply_suppressed_notify_failed"

                success = await self._send_reply(email)
                if success:
                    self._state.emails_replied += 1
                return "reply_sent" if success else "reply_failed"

            case EmailIntent.ASK_BOSS:
                success = await self._consult_boss(email, classification)
                if success:
                    self._state.boss_consultations += 1
                return "boss_consulted" if success else "boss_consultation_failed"

            case _:
                return "unknown_action"

    async def _send_notification(
        self, email: dict[str, Any], classification: EmailClassification,
        email_record_id: Optional[int] = None,
    ) -> bool:
        """Generate and send a tracked Telegram notification."""
        sender = email.get("sender", "")
        subject = email.get("subject", "")
        body = email.get("body", "")

        messages = notification_prompt(
            sender=sender, subject=subject, body=body[:1000],
            principal_name=self._principal_name,
        )

        try:
            result = await self.ctx.llm_pipeline.chat(
                messages=messages,
                audit_action="email_notification",
                skip_preflight=True,
            )
            if not result or result.blocked or not result.content:
                return False

            notification_text = (
                f"*Email from {sender}*\n"
                f"_{subject}_\n\n"
                f"{result.content.strip()}"
            )

            # Send via Telegram notifier capability (tracked when possible)
            notifier = self.ctx.get_capability("telegram_notifier")
            if not notifier:
                logger.warning("EmailAgent: telegram_notifier capability not available")
                return False

            # Use tracked send to enable feedback loop
            tg_message_id = await notifier.send_notification_tracked(
                notification_text, ref_id=str(email_record_id or ""),
            )

            if tg_message_id and email_record_id and self._db:
                chat_id = notifier._chat_id or ""
                await self._db.track_notification(
                    email_record_id=email_record_id,
                    tg_message_id=tg_message_id,
                    tg_chat_id=chat_id,
                    notification_text=notification_text,
                )

            return tg_message_id is not None

        except Exception as e:
            logger.error("EmailAgent: notification generation failed: %s", e)
            return False

    async def _request_research(self, query: str, context: str = "") -> Optional[str]:
        """Ask the supervisor for research via BossRequestCapability."""
        boss_cap = self.ctx.get_capability("boss_request")
        if not boss_cap or not boss_cap.configured:
            logger.debug("EmailAgent: boss_request capability not available for research")
            return None
        return await boss_cap.request_research(query, context)

    async def _send_reply(
        self, email: dict[str, Any], research_context: str = "",
    ) -> bool:
        """Generate and send an email reply via the Gmail plugin event bus."""
        sender = email.get("sender", "")
        subject = email.get("subject", "")
        body = email.get("body", "")

        # Get sender context from profile (GDPR-safe) and DB history
        profile = self._load_sender_profile(sender)
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

        if research_context:
            messages = reply_prompt_with_research(
                sender=sender,
                subject=subject,
                body=body[:3000],
                sender_context=sender_context,
                interaction_history=interaction_history,
                principal_name=self._principal_name,
                research_context=research_context,
            )
        else:
            messages = reply_prompt(
                sender=sender,
                subject=subject,
                body=body[:3000],
                sender_context=sender_context,
                interaction_history=interaction_history,
                principal_name=self._principal_name,
            )

        try:
            result = await self.ctx.llm_pipeline.chat(
                messages=messages,
                audit_action="email_reply",
            )
            if not result or result.blocked or not result.content:
                return False

            # Send via GmailCapability (thread-aware reply)
            gmail_cap = self.ctx.get_capability("gmail")
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
                if success:
                    # Mark original as read after successful reply
                    await gmail_cap.mark_as_read(msg_id)
                return success
            else:
                logger.warning("EmailAgent: gmail capability not available for reply")
                return False

        except Exception as e:
            logger.error("EmailAgent: reply generation failed: %s", e)
            return False

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
        consultant = self.ctx.get_capability("personality_consultant")
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

            # Parse JSON advice from consultant
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

    async def _consult_boss(
        self, email: dict[str, Any], classification: EmailClassification,
    ) -> bool:
        """Ask supervisor via IPC for guidance on an uncertain email."""
        if not self.ctx.ipc_client:
            logger.debug("EmailAgent: no IPC client available for boss consultation")
            return False

        sender = email.get("sender", "")
        subject = email.get("subject", "")
        snippet = email.get("snippet", email.get("body", "")[:200])

        # Generate question for boss
        messages = boss_consultation_prompt(
            sender=sender,
            subject=subject,
            snippet=snippet,
            reasoning=classification.reasoning,
            tentative_intent=classification.intent.value,
            confidence=classification.confidence,
        )

        question = "How should I handle this email?"
        try:
            result = await self.ctx.llm_pipeline.chat(
                messages=messages,
                audit_action="email_boss_question",
                skip_preflight=True,
            )
            if result and not result.blocked and result.content:
                question = result.content.strip()
        except Exception as e:
            logger.debug("EmailAgent: question generation failed: %s", e)

        # Send IPC message
        msg = IPCMessage(
            msg_type="email_consultation",
            payload={
                "question": question,
                "email_from": sender,
                "email_subject": subject,
                "tentative_intent": classification.intent.value,
                "confidence": classification.confidence,
            },
            sender=self.ctx.identity_name,
        )

        try:
            response = await self.ctx.ipc_client.send(msg, timeout=30.0)
            if response and response.payload:
                # Process boss guidance
                await self._process_boss_response(email, classification, response)
                return True
        except Exception as e:
            logger.error("EmailAgent: IPC consultation failed: %s", e)

        return False

    async def _process_boss_response(
        self,
        email: dict[str, Any],
        classification: EmailClassification,
        response: IPCMessage,
    ) -> None:
        """Process the supervisor's guidance and store as learning."""
        advised_action = response.payload.get("advised_action", "")
        reasoning = response.payload.get("reasoning", "")

        # Store learning from boss feedback
        if self._db and advised_action:
            await self._db.store_learning(AgentLearning(
                learning_type="classification",
                content=f"Boss advised '{advised_action}' for email from {email.get('sender', '')}: {reasoning}",
                source="boss_feedback",
                email_from=email.get("sender"),
            ))

            # Refresh learnings
            self._learnings = await self._db.get_learnings(limit=50)

        # Execute the advised action
        if advised_action:
            advised_classification = EmailClassification(
                intent=EmailIntent(advised_action),
                confidence=1.0,
                reasoning=f"Boss directed: {reasoning}",
                priority=classification.priority,
            )
            await self._execute_action(email, advised_classification)

    async def _update_sender_profile(
        self, sender: str, classification: EmailClassification,
    ) -> None:
        """
        Update the consolidated sender profile after each conversation.

        Profile files contain GDPR-safe aggregate data only:
        interaction counts, language preference, intent distribution.
        No email bodies, no personal content.
        """
        if not self._profiles_dir:
            return

        profile = self._load_sender_profile(sender)

        # Update aggregate stats
        profile.total_interactions += 1
        profile.last_interaction_date = datetime.now().strftime("%Y-%m-%d")
        profile.avg_confidence = (
            (profile.avg_confidence * (profile.total_interactions - 1) + classification.confidence)
            / profile.total_interactions
        )

        # Update intent distribution
        intent = classification.intent.value
        profile.intent_distribution[intent] = profile.intent_distribution.get(intent, 0) + 1

        # Save profile
        safe_name = sender.replace("@", "_at_").replace(".", "_")
        profile_path = self._profiles_dir / f"{safe_name}.json"
        try:
            profile_path.write_text(json.dumps(profile.model_dump(), indent=2))
        except Exception as e:
            logger.error("EmailAgent: failed to save sender profile for %s: %s", sender, e)

    def _load_sender_profile(self, sender: str) -> SenderProfile:
        """Load a sender profile from disk, or create a new one."""
        if not self._profiles_dir:
            return SenderProfile(email=sender)

        safe_name = sender.replace("@", "_at_").replace(".", "_")
        profile_path = self._profiles_dir / f"{safe_name}.json"

        if profile_path.exists():
            try:
                data = json.loads(profile_path.read_text())
                return SenderProfile(**data)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("EmailAgent: failed to load sender profile: %s", e)

        return SenderProfile(email=sender)

    async def _check_tg_feedback(self) -> None:
        """Check for principal's Telegram feedback on sent notifications."""
        notifier = self.ctx.get_capability("telegram_notifier")
        if not notifier or not notifier.configured:
            return

        if not self._db:
            return

        try:
            updates = await notifier.fetch_updates()
        except Exception as e:
            logger.debug("EmailAgent: failed to fetch TG updates: %s", e)
            return

        for update in updates:
            # Only process replies to our tracked notifications
            if not update.reply_to_message_id:
                continue

            # Look up the original notification
            tracking = await self._db.get_notification_by_tg_id(
                update.reply_to_message_id,
            )
            if not tracking:
                continue

            # Classify the feedback via LLM
            sentiment, learning_text, should_ack = await self._classify_feedback(
                feedback_text=update.text,
                original_notification=tracking.get("notification_text", ""),
                original_email_subject=tracking.get("email_subject", ""),
            )

            # Record feedback in DB
            await self._db.record_feedback(
                tracking_id=tracking["id"],
                text=update.text,
                sentiment=sentiment,
            )

            # Update the email record with feedback
            was_correct = sentiment == "positive"
            email_record_id = tracking.get("email_record_id")
            if email_record_id:
                await self._db.update_feedback(
                    record_id=email_record_id,
                    feedback=update.text,
                    was_correct=was_correct,
                )

            # Store as learning
            if learning_text:
                await self._db.store_learning(AgentLearning(
                    learning_type="classification",
                    content=learning_text,
                    source="principal_feedback",
                    email_from=tracking.get("email_from"),
                ))
                # Refresh learnings cache
                self._learnings = await self._db.get_learnings(limit=50)

            # Acknowledge if appropriate
            if should_ack and notifier.configured:
                await notifier.send_notification(
                    "Noted, I'll adjust my classification accordingly."
                )

            logger.info(
                "Processed TG feedback (sentiment=%s) for email '%s'",
                sentiment, tracking.get("email_subject", ""),
            )

    async def _classify_feedback(
        self, feedback_text: str, original_notification: str,
        original_email_subject: str,
    ) -> tuple[str, str, bool]:
        """Classify principal feedback via LLM. Returns (sentiment, learning, should_ack)."""
        if not self.ctx.llm_pipeline:
            # Heuristic fallback
            lower = feedback_text.lower()
            if any(w in lower for w in ("bra", "tack", "great", "good", "thanks")):
                return "positive", "", False
            if any(w in lower for w in ("inte", "sluta", "stop", "spam", "no")):
                return "negative", f"Principal said '{feedback_text}' about {original_email_subject}", True
            return "neutral", "", False

        messages = feedback_classification_prompt(
            feedback_text=feedback_text,
            original_notification=original_notification,
            original_email_subject=original_email_subject,
        )

        try:
            result = await self.ctx.llm_pipeline.chat(
                messages=messages,
                audit_action="feedback_classification",
                skip_preflight=True,
            )
            if result and not result.blocked and result.content:
                return self._parse_feedback_classification(result.content)
        except Exception as e:
            logger.debug("EmailAgent: feedback classification failed: %s", e)

        return "neutral", "", False

    def _parse_feedback_classification(self, raw: str) -> tuple[str, str, bool]:
        """Parse LLM feedback classification JSON."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start:end])
                except json.JSONDecodeError:
                    return "neutral", "", False
            else:
                return "neutral", "", False

        sentiment = data.get("sentiment", "neutral")
        if sentiment not in ("positive", "negative", "neutral"):
            sentiment = "neutral"
        learning = data.get("learning", "")
        should_ack = data.get("should_acknowledge", False)
        return sentiment, learning, should_ack

    def _is_allowed_sender(self, sender: str) -> bool:
        """Check if sender is allowed based on filter mode."""
        if self._filter_mode == "opt_in":
            return sender in self._allowed_senders
        elif self._filter_mode == "opt_out":
            return sender not in self._blocked_senders
        return True

    def _build_system_prompt(self) -> str:
        """Build system prompt from Stål's personality."""
        from overblick.personalities import build_system_prompt, load_personality

        try:
            personality = load_personality("stal")
            return build_system_prompt(personality, platform="Email")
        except FileNotFoundError:
            principal = self._principal_name or "the principal"
            return (
                f"You are Stål, digital assistant to {principal}. "
                "Be professional, precise, and respond in the sender's language."
            )

    async def _initialize_default_goals(self) -> None:
        """Set up default goals for a new agent."""
        if not self._db:
            return

        for goal in _DEFAULT_GOALS:
            goal_id = await self._db.upsert_goal(goal)
            goal.id = goal_id
            self._state.goals.append(goal)

        logger.info("EmailAgent: initialized %d default goals", len(_DEFAULT_GOALS))

    def get_status(self) -> dict:
        """Expose status for dashboard."""
        return {
            "plugin": self.name,
            "identity": self.ctx.identity_name,
            "emails_processed": self._state.emails_processed,
            "emails_replied": self._state.emails_replied,
            "notifications_sent": self._state.notifications_sent,
            "boss_consultations": self._state.boss_consultations,
            "confidence_threshold": self._state.confidence_threshold,
            "active_goals": len(self._state.goals),
            "learnings_count": len(self._learnings),
            "health": self._state.current_health,
        }

    async def teardown(self) -> None:
        """Cleanup database connection."""
        if self._db:
            await self._db.close()
        logger.info("EmailAgentPlugin teardown complete")
