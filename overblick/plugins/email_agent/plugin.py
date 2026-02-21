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

import asyncio
import email.utils
import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from overblick.capabilities.consulting.personality_consultant import (
    PersonalityConsultantCapability,
)
from overblick.core.plugin_base import PluginBase, PluginContext
from overblick.core.security.input_sanitizer import wrap_external_content
from overblick.plugins.email_agent.classifier import EmailClassifier
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
    feedback_classification_prompt,
    notification_prompt,
)
from overblick.plugins.email_agent.reply_generator import ReplyGenerator
from overblick.plugins.email_agent.reputation import ReputationManager
from overblick.supervisor.ipc import IPCMessage

logger = logging.getLogger(__name__)

# Default max email age when not configured — prevents processing months-old backlog
_DEFAULT_MAX_EMAIL_AGE_HOURS = 48

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

    Internal helpers:
      - EmailClassifier  — LLM classification, intent normalisation, context building
      - ReputationManager — sender/domain reputation and profile persistence
      - ReplyGenerator   — reply composition, tone consultation, draft notifications
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
        self._dry_run: bool = False
        self._max_email_age_hours: Optional[float] = None
        self._show_draft_replies: bool = False
        # Reputation system config (learned thresholds)
        self._auto_ignore_sender_threshold: float = 0.9
        self._auto_ignore_sender_min_count: int = 5
        self._auto_ignore_domain_threshold: float = 0.9
        self._auto_ignore_domain_min_count: int = 10
        # Cross-identity consultation config
        self._relevance_consultants: list[dict] = []
        self._consultation_confidence_low: float = 0.5
        self._consultation_confidence_high: float = 0.8
        self._consultation_identities: str = "explicit"  # "all" or "explicit"
        self._discovered_consultants: dict[str, list[str]] = {}
        # Cached auto-ignore domains
        self._auto_ignore_domains: set[str] = set()
        # Helper instances (set in setup())
        self._classifier: Optional[EmailClassifier] = None
        self._reputation: Optional[ReputationManager] = None
        self._reply_gen: Optional[ReplyGenerator] = None

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

        try:
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
        except Exception:
            # Clean up DB if setup fails after DB init
            if self._db:
                try:
                    await self._db.close()
                except Exception as close_err:
                    logger.warning("EmailAgent: DB close failed during setup cleanup: %s", close_err)
                self._db = None
            raise

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

        # Max email age — skip emails older than this (hours).
        age_val = ea_config.get("max_email_age_hours")
        if age_val is not None:
            try:
                parsed = float(age_val)
            except (ValueError, TypeError):
                parsed = None
            if parsed is not None and parsed > 0 and not math.isinf(parsed) and not math.isnan(parsed):
                self._max_email_age_hours = parsed
            else:
                logger.warning(
                    "EmailAgent: invalid max_email_age_hours=%r — must be positive number, "
                    "falling back to default %dh",
                    age_val, _DEFAULT_MAX_EMAIL_AGE_HOURS,
                )
                self._max_email_age_hours = _DEFAULT_MAX_EMAIL_AGE_HOURS
        else:
            self._max_email_age_hours = _DEFAULT_MAX_EMAIL_AGE_HOURS
            logger.info(
                "EmailAgent: max_email_age_hours not configured — defaulting to %dh",
                _DEFAULT_MAX_EMAIL_AGE_HOURS,
            )

        # Dry-run mode: classify and notify, but never send actual email replies
        self._dry_run = ea_config.get("dry_run", False)
        if self._dry_run:
            logger.info("EmailAgentPlugin running in DRY RUN mode — no emails will be sent")

        # Draft replies: send a second Telegram message with Stål's suggested reply
        self._show_draft_replies = ea_config.get("show_draft_replies", False)
        if self._show_draft_replies:
            logger.info("EmailAgentPlugin: draft reply mode enabled — suggested replies will be sent to Telegram")

        # Reputation thresholds (configurable, not hardcoded)
        reputation_config = ea_config.get("reputation", {})
        self._auto_ignore_sender_threshold = reputation_config.get("sender_ignore_rate", 0.9)
        self._auto_ignore_sender_min_count = reputation_config.get("sender_min_interactions", 5)
        self._auto_ignore_domain_threshold = reputation_config.get("domain_ignore_rate", 0.9)
        self._auto_ignore_domain_min_count = reputation_config.get("domain_min_interactions", 10)

        # Cross-identity consultation config
        self._relevance_consultants = ea_config.get("relevance_consultants", [])
        consultation_config = ea_config.get("consultation", {})
        self._consultation_confidence_low = consultation_config.get("confidence_low", 0.5)
        self._consultation_confidence_high = consultation_config.get("confidence_high", 0.8)
        self._consultation_identities = consultation_config.get("identities", "explicit")

        # Load cached auto-ignore domains from DB
        if self._db:
            self._auto_ignore_domains = set(await self._db.get_auto_ignore_domains())

        # Run GDPR cleanup on startup
        await self._db.purge_gdpr_data(self.GDPR_RETENTION_DAYS)

        # Check interval from schedule
        self._check_interval = identity.schedule.feed_poll_minutes * 60

        # Wire helper instances
        self._reputation = ReputationManager(
            db=self._db,
            profiles_dir=self._profiles_dir,
            thresholds={
                "sender_ignore_rate": self._auto_ignore_sender_threshold,
                "sender_min_interactions": self._auto_ignore_sender_min_count,
                "domain_ignore_rate": self._auto_ignore_domain_threshold,
                "domain_min_interactions": self._auto_ignore_domain_min_count,
            },
        )
        self._classifier = EmailClassifier(
            ctx=self.ctx,
            state=self._state,
            learnings=self._learnings,
            db=self._db,
            principal_name=self._principal_name,
            allowed_senders=self._allowed_senders,
            filter_mode=self._filter_mode,
            blocked_senders=self._blocked_senders,
        )
        self._reply_gen = ReplyGenerator(
            ctx=self.ctx,
            principal_name=self._principal_name,
            db=self._db,
            reputation=self._reputation,
        )

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

        if self._state.last_check and (now - self._state.last_check < self._check_interval):
            return

        if self.ctx.quiet_hours_checker and self.ctx.quiet_hours_checker.is_quiet_hours():
            return

        if not self.ctx.llm_pipeline:
            logger.debug("EmailAgent: no LLM pipeline available")
            return

        self._state.last_check = now

        try:
            emails = await self._fetch_unread()
            for email in emails:
                await self._process_email(email)
            await self._check_tg_feedback()
        except Exception as e:
            logger.error("EmailAgent tick failed: %s", e, exc_info=True)
            self._state.current_health = "degraded"

    async def _fetch_unread(self) -> list[dict[str, Any]]:
        """
        Fetch unread emails via GmailCapability.

        Converts GmailMessage objects to dicts for the classification pipeline.
        Filters out emails older than max_email_age_hours (if configured).
        """
        logger.debug("EmailAgent: checking for unread emails")

        gmail_cap = self.ctx.get_capability("gmail")
        if not gmail_cap:
            logger.debug("EmailAgent: gmail capability not available")
            return []

        since_days: Optional[int] = None
        if self._max_email_age_hours is not None:
            since_days = max(1, math.ceil(self._max_email_age_hours / 24))

        messages = await gmail_cap.fetch_unread(max_results=10, since_days=since_days)

        results = []
        for msg in messages:
            msg_dict = {
                "message_id": msg.message_id,
                "thread_id": msg.thread_id,
                "sender": msg.sender,
                "subject": msg.subject,
                "body": msg.body,
                "snippet": msg.snippet,
                "headers": {**msg.headers, "Date": msg.timestamp},
            }

            if self._max_email_age_hours is not None:
                if not self._is_recent_email(msg_dict, self._max_email_age_hours):
                    logger.info(
                        "EmailAgent: skipping old email from %s (subject: %s) — "
                        "older than %.1f hours",
                        msg.sender, msg.subject, self._max_email_age_hours,
                    )
                    await self._mark_email_read(msg.message_id)
                    continue

            results.append(msg_dict)

        return results

    @staticmethod
    def _is_recent_email(msg: dict[str, Any], max_hours: float) -> bool:
        """Check if an email is recent enough to process.

        Parses the Date header (RFC 2822) and compares against max_hours.
        Returns True if the email is recent or if the date cannot be parsed.
        """
        headers = msg.get("headers", {})
        date_str = headers.get("Date") or headers.get("date")
        if not date_str:
            logger.warning("EmailAgent: no Date header — treating as old (fail-closed)")
            return False

        try:
            msg_dt = email.utils.parsedate_to_datetime(date_str)
            if msg_dt.tzinfo is None:
                msg_dt = msg_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            age_hours = (now - msg_dt).total_seconds() / 3600.0
            return age_hours <= max_hours
        except (ValueError, TypeError):
            logger.warning("EmailAgent: unparseable Date header — treating as old (fail-closed)")
            return False

    async def _mark_email_read(self, message_id: str) -> None:
        """Mark an email as read in Gmail to prevent re-processing."""
        if not message_id:
            return
        gmail_cap = self.ctx.get_capability("gmail")
        if not gmail_cap:
            return
        try:
            await gmail_cap.mark_as_read(message_id)
        except Exception as e:
            logger.warning("EmailAgent: failed to mark email %s as read: %s", message_id, e)

    async def _process_email(self, email: dict[str, Any]) -> None:
        """
        Process a single email through the classification pipeline.

        Flow:
        1. Deduplication check
        2. Get sender + domain reputation
        3. If auto-ignore (learned) → skip LLM entirely
        4. Classify via LLM with reputation context + header signals
        5. If NOTIFY with moderate confidence → consult relevant identity
        6. Confidence check → ASK_BOSS if low
        7. Execute action
        8. Update sender + domain reputation
        """
        sender = email.get("sender", "")
        subject = email.get("subject", "")
        body = email.get("body", "")
        snippet = email.get("snippet", body[:200])
        message_id = email.get("message_id", "")
        headers = email.get("headers", {})

        # 1. Deduplication
        if message_id and self._db and await self._db.has_been_processed(message_id):
            logger.debug("EmailAgent: skipping already-processed email %s", message_id)
            return

        # 2. Get sender + domain reputation
        sender_rep = await self._reputation.get_sender_reputation(sender)
        domain_rep = await self._reputation.get_domain_reputation(sender)

        # 3. Auto-ignore check (learned, not hardcoded)
        domain = self._reputation.extract_domain(sender)
        if (
            self._reputation.should_auto_ignore_sender(sender_rep)
            or self._reputation.should_auto_ignore_domain(domain_rep)
            or domain in self._auto_ignore_domains
        ):
            logger.info(
                "EmailAgent: auto-ignoring email from %s (learned reputation)",
                sender,
            )
            classification = EmailClassification(
                intent=EmailIntent.IGNORE,
                confidence=0.99,
                reasoning="Auto-ignored based on learned sender/domain reputation",
                priority="low",
            )
            if self._db:
                await self._db.record_email(EmailRecord(
                    gmail_message_id=message_id,
                    email_from=sender,
                    email_subject=subject,
                    email_snippet=snippet,
                    classified_intent="ignore",
                    confidence=0.99,
                    reasoning="Auto-ignored (learned reputation)",
                    action_taken="auto_ignored",
                ))
            self._state.emails_processed += 1
            await self._mark_email_read(message_id)
            await self._reputation.update_sender_profile(sender, classification)
            if self._db and domain:
                await self._db.update_domain_stats(domain, "ignore")
            return

        # 4. Wrap external content and classify via LLM with context
        safe_subject = wrap_external_content(subject, "email_subject")
        safe_body = wrap_external_content(body[:3000], "email_body")

        reputation_context = self._classifier.build_reputation_context(sender_rep, domain_rep)
        email_signals = self._classifier.build_email_signals(headers)

        classification = await self._classifier.classify(
            sender, safe_subject, safe_body,
            sender_reputation=reputation_context,
            email_signals=email_signals,
        )
        if not classification:
            logger.warning("EmailAgent: classification failed for email from %s", sender)
            return

        # 5. Cross-identity consultation for moderate-confidence NOTIFY
        if (
            classification.intent == EmailIntent.NOTIFY
            and self._consultation_confidence_low
            <= classification.confidence
            <= self._consultation_confidence_high
        ):
            advice = await self._consult_identity_relevance(email, classification)
            if advice and advice.strip().upper().startswith("NO"):
                logger.info(
                    "EmailAgent: identity consultation downgraded NOTIFY → IGNORE for %s",
                    sender,
                )
                classification.intent = EmailIntent.IGNORE
                classification.reasoning += " [downgraded by identity consultation]"

        # 6. Confidence check — below threshold triggers ask_boss
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
                gmail_message_id=message_id,
                email_from=sender,
                email_subject=subject,
                email_snippet=snippet,
                classified_intent=classification.intent.value,
                confidence=classification.confidence,
                reasoning=classification.reasoning,
                action_taken="pending",
            ))

        # 7. Execute action
        action_taken = await self._execute_action(
            email, classification, email_record_id=email_record_id,
        )

        if self._db and email_record_id:
            await self._db.update_action_taken(email_record_id, action_taken)

        self._state.emails_processed += 1
        await self._mark_email_read(message_id)

        # 8. Update sender profile + domain reputation
        await self._reputation.update_sender_profile(sender, classification)
        if self._db and domain:
            await self._db.update_domain_stats(domain, classification.intent.value)

        logger.info(
            "EmailAgent: processed email from %s — %s (confidence: %.2f)",
            sender, classification.intent.value, classification.confidence,
        )

    def _build_consultant_registry(self) -> dict[str, list[str]]:
        """
        Build the identity → keywords registry for consultation.

        In "all" mode, auto-discovers from disk (lazy, cached).
        In "explicit" mode, uses the legacy relevance_consultants list.
        """
        if self._consultation_identities == "all":
            if not self._discovered_consultants:
                consultant = self.ctx.get_capability("personality_consultant")
                if consultant and hasattr(consultant, "discover_consultants"):
                    self._discovered_consultants = consultant.discover_consultants(
                        exclude={"supervisor"},
                    )
            return self._discovered_consultants

        return {
            entry["identity"]: entry.get("keywords", [])
            for entry in self._relevance_consultants
        }

    async def _consult_identity_relevance(
        self,
        email: dict[str, Any],
        classification: EmailClassification,
    ) -> Optional[str]:
        """
        Consult a relevant identity about whether an email is worth notifying.

        Only triggered for NOTIFY with moderate confidence. Returns the identity's
        advice, or None if consultation unavailable or not triggered.
        """
        consultant = self.ctx.get_capability("personality_consultant")
        if not consultant:
            return None

        registry = self._build_consultant_registry()
        if not registry:
            return None

        subject_lower = email.get("subject", "").lower()
        body_lower = email.get("body", "")[:500].lower()
        text = f"{subject_lower} {body_lower}"

        best_identity = None
        best_score = 0
        for identity_name, keywords in registry.items():
            score = PersonalityConsultantCapability.score_match(text, keywords)
            if score > best_score:
                best_score = score
                best_identity = identity_name

        if not best_identity:
            return None

        query = (
            "Is this email content relevant and worth notifying the principal about? "
            "Consider whether it contains actionable information, important updates, "
            "or genuinely interesting content — or if it's just noise.\n\n"
            f"From: {email.get('sender', '')}\n"
            f"Subject: {email.get('subject', '')}\n"
            f"Snippet: {email.get('snippet', '')[:300]}\n\n"
            "Respond YES or NO with brief reasoning."
        )

        try:
            response = await consultant.consult(
                query=query,
                consultant_name=best_identity,
            )
            if response:
                logger.info(
                    "EmailAgent: %s consultation on '%s' (score=%d) → %s",
                    best_identity,
                    email.get("subject", ""),
                    best_score,
                    response[:100],
                )
            return response
        except Exception as e:
            logger.debug("EmailAgent: identity consultation failed: %s", e)
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
                if success and self._show_draft_replies:
                    notifier = self.ctx.get_capability("telegram_notifier")
                    draft_result = await self._reply_gen.send_draft_notification(
                        email, notifier,
                    )
                    if draft_result and email_record_id and self._db:
                        tg_id, draft_body = draft_result
                        await self._db.track_draft_notification(
                            email_record_id=email_record_id,
                            tg_message_id=tg_id,
                            tg_chat_id=notifier.chat_id,
                            draft_reply_body=draft_body,
                            original_thread_id=email.get("thread_id", ""),
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
                    success = await self._send_notification(
                        email, classification, email_record_id=email_record_id,
                    )
                    if success:
                        self._state.notifications_sent += 1
                    return "reply_suppressed_notify_fallback" if success else "reply_suppressed_notify_failed"

                if self._dry_run:
                    logger.info("DRY RUN: would reply to %s re: %s — skipped", sender, email.get("subject", ""))
                    return "dry_run_reply_skipped"

                success = await self._reply_gen.generate_and_send(email)
                if success:
                    self._state.emails_replied += 1
                return "reply_sent" if success else "reply_failed"

            case EmailIntent.ASK_BOSS:
                success = await self._consult_boss(email, classification)
                if success:
                    self._state.boss_consultations += 1
                    return "boss_consulted"
                logger.info(
                    "EmailAgent: boss consultation failed for %s — falling back to notify",
                    email.get("sender", ""),
                )
                fallback_ok = await self._send_notification(
                    email, classification, email_record_id=email_record_id,
                )
                if fallback_ok:
                    self._state.notifications_sent += 1
                return "boss_unavailable_notify_fallback" if fallback_ok else "boss_unavailable_notify_failed"

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

        safe_sender = wrap_external_content(sender, "email_sender")
        safe_subject = wrap_external_content(subject, "email_subject")
        safe_body = wrap_external_content(body[:1000], "email_body")
        messages = notification_prompt(
            sender=safe_sender, subject=safe_subject, body=safe_body,
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

            notifier = self.ctx.get_capability("telegram_notifier")
            if not notifier:
                logger.warning("EmailAgent: telegram_notifier capability not available")
                return False

            tg_message_id = await notifier.send_notification_tracked(
                notification_text, ref_id=str(email_record_id or ""),
            )

            if tg_message_id and email_record_id and self._db:
                chat_id = notifier.chat_id
                await self._db.track_notification(
                    email_record_id=email_record_id,
                    tg_message_id=tg_message_id,
                    tg_chat_id=chat_id,
                    notification_text=notification_text,
                )

            return tg_message_id is not None

        except Exception as e:
            logger.error("EmailAgent: notification generation failed: %s", e, exc_info=True)
            return False

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
                await self._process_boss_response(email, classification, response)
                return True
        except Exception as e:
            logger.error("EmailAgent: IPC consultation failed: %s", e, exc_info=True)

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

        normalized = EmailClassifier.normalize_intent(advised_action) if advised_action else None
        if not normalized:
            return

        if self._db:
            await self._db.store_learning(AgentLearning(
                learning_type="classification",
                content=f"Boss advised '{normalized}' for email from {email.get('sender', '')}: {reasoning}",
                source="boss_feedback",
                email_from=email.get("sender"),
            ))
            self._learnings = await self._db.get_learnings(limit=50)

        advised_classification = EmailClassification(
            intent=EmailIntent(normalized),
            confidence=1.0,
            reasoning=f"Boss directed: {reasoning}",
            priority=classification.priority,
        )
        await self._execute_action(email, advised_classification)

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
            if not update.reply_to_message_id:
                continue

            tracking = await self._db.get_notification_by_tg_id(
                update.reply_to_message_id,
            )
            if not tracking:
                continue

            # Check if this is an approval of a draft reply
            if tracking.get("is_draft_reply") and self._is_send_approval(update.text):
                await self._send_approved_draft(tracking, notifier)
                continue

            sentiment, learning_text, should_ack = await self._classify_feedback(
                feedback_text=update.text,
                original_notification=tracking.get("notification_text", ""),
                original_email_subject=tracking.get("email_subject", ""),
            )

            await self._db.record_feedback(
                tracking_id=tracking["id"],
                text=update.text,
                sentiment=sentiment,
            )

            was_correct = sentiment == "positive"
            email_record_id = tracking.get("email_record_id")
            if email_record_id:
                await self._db.update_feedback(
                    record_id=email_record_id,
                    feedback=update.text,
                    was_correct=was_correct,
                )

            email_from = tracking.get("email_from", "")
            if learning_text:
                learning_type = "classification"
                if sentiment == "negative":
                    learning_type = "sender_reputation"
                    learning_text = f"IGNORE emails from {email_from}: {learning_text}"
                await self._db.store_learning(AgentLearning(
                    learning_type=learning_type,
                    content=learning_text,
                    source="principal_feedback",
                    email_from=email_from,
                ))
                self._learnings = await self._db.get_learnings(limit=50)

            if email_from:
                domain = self._reputation.extract_domain(email_from)
                if domain:
                    await self._db.update_domain_stats(domain, "", feedback=sentiment)
                    domain_rep = await self._reputation.get_domain_reputation(email_from)
                    if self._reputation.should_auto_ignore_domain(domain_rep):
                        await self._db.set_auto_ignore(domain, True)
                        self._auto_ignore_domains.add(domain)
                        logger.info(
                            "EmailAgent: auto-ignore enabled for domain %s after negative feedback",
                            domain,
                        )

            if sentiment == "negative" and email_from:
                await self._reputation.penalize_sender(email_from)

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

    # -- Draft approval --

    _APPROVAL_WORDS = frozenset({
        "skicka", "send", "ja", "yes", "ok", "approve", "godkänn", "\U0001f44d",
    })

    def _is_send_approval(self, text: str) -> bool:
        """Check if text is an approval to send a draft reply."""
        return text.strip().lower() in self._APPROVAL_WORDS

    async def _send_approved_draft(self, tracking: dict, notifier: Any) -> None:
        """Send a previously approved draft reply as a real email."""
        draft_body = tracking.get("draft_reply_body", "")
        if not draft_body:
            await notifier.send_notification("Could not send — draft text not found.")
            return

        email_from = tracking.get("email_from", "")
        email_subject = tracking.get("email_subject", "")
        thread_id = tracking.get("original_email_thread_id", "")
        message_id = tracking.get("gmail_message_id", "")

        if not self._is_allowed_sender(email_from):
            await notifier.send_notification(
                f"Cannot send — {email_from} is not in the allowed senders list.",
            )
            return

        gmail_cap = self.ctx.get_capability("gmail")
        if not gmail_cap:
            await notifier.send_notification("Gmail not available — could not send reply.")
            return

        reply_subject = (
            email_subject if email_subject.startswith("Re:") else f"Re: {email_subject}"
        )
        success = await gmail_cap.send_reply(
            thread_id=thread_id,
            message_id=message_id,
            to=email_from,
            subject=reply_subject,
            body=draft_body,
        )

        if success:
            self._state.emails_replied += 1
            await notifier.send_notification(f"Reply sent to {email_from}")
            if self._db and tracking.get("email_record_id"):
                await self._db.update_action_taken(
                    tracking["email_record_id"], "draft_approved_sent",
                )
        else:
            await notifier.send_notification(f"Failed to send reply to {email_from}")

    def _is_allowed_sender(self, sender: str) -> bool:
        """Check if sender is allowed based on filter mode."""
        if self._filter_mode == "opt_in":
            return sender in self._allowed_senders
        elif self._filter_mode == "opt_out":
            return sender not in self._blocked_senders
        return True

    def _build_system_prompt(self) -> str:
        """Build system prompt from Stål's personality."""
        try:
            personality = self.ctx.load_identity("stal")
            return self.ctx.build_system_prompt(personality, platform="Email")
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
