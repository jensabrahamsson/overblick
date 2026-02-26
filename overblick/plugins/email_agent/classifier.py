"""
Email classifier — LLM-based intent classification for incoming emails.

Owns all classification logic: building prompts, calling the LLM pipeline,
parsing and normalising responses, and building signal/reputation context strings.

Security layers (applied before LLM):
1. Blocked domains — hardcoded high-risk domains auto-ignored
2. Phishing detection — urgency/link patterns auto-ignored
3. LLM classification — intent + confidence
"""

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Optional

from overblick.plugins.email_agent.models import (
    AgentGoal,
    AgentLearning,
    AgentState,
    EmailClassification,
    EmailIntent,
)
from overblick.plugins.email_agent.prompts import classification_prompt

if TYPE_CHECKING:
    from overblick.core.plugin_base import PluginContext
    from overblick.plugins.email_agent.database import EmailAgentDB

logger = logging.getLogger(__name__)

# Domains known to be high-risk or exclusively used for spam/phishing.
# Emails from these domains are auto-ignored before reaching the LLM.
BLOCKED_DOMAINS: frozenset[str] = frozenset({
    "tempmail.com", "throwaway.email", "guerrillamail.com",
    "mailinator.com", "yopmail.com", "sharklasers.com",
    "guerrillamailblock.com", "grr.la", "dispostable.com",
    "trashmail.com", "fakeinbox.com", "maildrop.cc",
})

# Regex patterns that indicate phishing or social engineering attempts.
# Matched against subject + body (case-insensitive).
_PHISHING_PATTERNS: list[re.Pattern] = [
    re.compile(r"verify\s+your\s+(account|identity|payment)", re.IGNORECASE),
    re.compile(r"(click|log\s*in)\s+(here|now|immediately)", re.IGNORECASE),
    re.compile(r"your\s+account\s+(has been|will be)\s+(suspended|locked|closed)", re.IGNORECASE),
    re.compile(r"(urgent|immediate)\s+(action|attention|response)\s+required", re.IGNORECASE),
    re.compile(r"confirm\s+your\s+(identity|password|credentials)", re.IGNORECASE),
    re.compile(r"unusual\s+(activity|sign.?in|login)\s+(detected|attempt)", re.IGNORECASE),
]

# Map common LLM-hallucinated intents to valid EmailIntent values
_INTENT_ALIASES: dict[str, str] = {
    "escalate": "ask_boss",
    "verify": "ask_boss",
    "flag": "ask_boss",
    "report": "ask_boss",
    "block": "ignore",
    "spam": "ignore",
    "delete": "ignore",
    "archive": "ignore",
    "skip": "ignore",
    "forward": "notify",
    "alert": "notify",
    "respond": "reply",
    "answer": "reply",
}


class EmailClassifier:
    """
    LLM-based email intent classifier.

    Wraps classification prompts, retry logic, JSON parsing, intent normalisation,
    and context-building helpers for reputation and email header signals.
    """

    def __init__(
        self,
        ctx: "PluginContext",
        state: AgentState,
        learnings: list[AgentLearning],
        db: Optional["EmailAgentDB"],
        principal_name: str,
        allowed_senders: set[str],
        filter_mode: str = "opt_in",
        blocked_senders: set[str] | None = None,
    ) -> None:
        self._ctx = ctx
        self._state = state
        self._learnings = learnings
        self._db = db
        self._principal_name = principal_name
        self._allowed_senders = allowed_senders
        self._filter_mode = filter_mode
        self._blocked_senders = blocked_senders or set()

    def _build_reply_policy(self) -> str:
        """Build a reply policy string based on filter mode."""
        if self._filter_mode == "opt_in":
            if self._allowed_senders:
                return f"Allowed reply addresses: {', '.join(sorted(self._allowed_senders))}"
            return "No senders are allowed for replies (empty allow-list)"
        # opt_out mode
        if self._blocked_senders:
            return (
                "Can reply to any sender except: "
                f"{', '.join(sorted(self._blocked_senders))}"
            )
        return "Can reply to any sender"

    @staticmethod
    def is_blocked_domain(sender: str) -> bool:
        """Check if sender's domain is in the blocked list."""
        try:
            domain = sender.rsplit("@", 1)[-1].lower().strip()
            return domain in BLOCKED_DOMAINS
        except (IndexError, AttributeError):
            return False

    @staticmethod
    def detect_phishing(subject: str, body: str) -> bool:
        """Check for common phishing/social engineering patterns."""
        text = f"{subject} {body[:2000]}"
        return any(p.search(text) for p in _PHISHING_PATTERNS)

    async def classify(
        self,
        sender: str,
        subject: str,
        body: str,
        sender_reputation: str = "",
        email_signals: str = "",
    ) -> Optional[EmailClassification]:
        """Run classification with pre-LLM safety checks, then LLM decision."""
        # Pre-LLM safety: blocked domains
        if self.is_blocked_domain(sender):
            logger.info(
                "EmailAgent: sender %s blocked (domain blocklist), auto-ignore",
                sender,
            )
            return EmailClassification(
                intent=EmailIntent.IGNORE,
                confidence=1.0,
                reasoning="Sender domain is on the blocklist",
                priority="low",
            )

        # Pre-LLM safety: phishing detection
        if self.detect_phishing(subject, body):
            logger.warning(
                "EmailAgent: phishing pattern detected in email from %s: %s",
                sender, subject[:80],
            )
            return EmailClassification(
                intent=EmailIntent.IGNORE,
                confidence=0.95,
                reasoning="Phishing/social engineering pattern detected",
                priority="low",
            )
        goals_text = "\n".join(
            f"- {g.description} (priority: {g.priority})"
            for g in self._state.goals
        ) or "No active goals"

        learnings_text = "\n".join(
            f"- [{l.learning_type}] {l.content}"
            for l in self._learnings[:10]
        ) or "No learnings yet"

        sender_history_text = "No previous interactions"
        if self._db:
            history = await self._db.get_sender_history(sender, limit=5)
            if history:
                sender_history_text = "\n".join(
                    f"- {r.email_subject}: {r.classified_intent} (confidence: {r.confidence:.2f})"
                    for r in history
                )

        # Build reply policy string based on filter mode
        reply_policy = self._build_reply_policy()

        messages = classification_prompt(
            goals=goals_text,
            learnings=learnings_text,
            sender_history=sender_history_text,
            sender=sender,
            subject=subject,
            body=body,
            principal_name=self._principal_name,
            reply_policy=reply_policy,
            sender_reputation=sender_reputation,
            email_signals=email_signals,
        )

        try:
            result = await self._ctx.llm_pipeline.chat(
                messages=messages,
                audit_action="email_classification",
                skip_preflight=True,
            )
            if result and not result.blocked and result.content:
                parsed = self._parse(result.content)
                if parsed:
                    return parsed
                # LLM returned prose instead of JSON — retry with explicit reminder
                retry_messages = list(messages) + [
                    {"role": "assistant", "content": result.content},
                    {"role": "user", "content": (
                        'Your response must be valid JSON only. '
                        'Respond with ONLY this JSON object, no other text:\n'
                        '{"intent": "ignore|notify|reply|ask_boss", "confidence": 0.0-1.0, '
                        '"reasoning": "one sentence", "priority": "low|normal|high|urgent"}'
                    )},
                ]
                retry_result = await self._ctx.llm_pipeline.chat(
                    messages=retry_messages,
                    audit_action="email_classification_retry",
                    skip_preflight=True,
                )
                if retry_result and not retry_result.blocked and retry_result.content:
                    return self._parse(retry_result.content)
        except Exception as e:
            logger.error("EmailAgent: classification LLM call failed: %s", e, exc_info=True)

        return None

    def _parse(self, raw: str) -> Optional[EmailClassification]:
        """Parse LLM JSON output into EmailClassification."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
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
            intent_str = data.get("intent", "ignore").lower().strip()
            normalized = self.normalize_intent(intent_str)
            if not normalized:
                normalized = "ignore"
                logger.info(
                    "EmailAgent: unrecognizable LLM intent '%s' — defaulting to ignore",
                    intent_str,
                )
            intent = EmailIntent(normalized)
            return EmailClassification(
                intent=intent,
                confidence=float(data.get("confidence", 0.5)),
                reasoning=str(data.get("reasoning", "")),
                priority=str(data.get("priority", "normal")),
            )
        except (ValueError, KeyError) as e:
            logger.warning("EmailAgent: invalid classification data: %s", e)
            return None

    @staticmethod
    def normalize_intent(raw_intent: str) -> Optional[str]:
        """Normalize a raw intent string to a valid EmailIntent value.

        Returns the normalized intent string or None if unrecognizable.
        """
        clean = raw_intent.strip().lower()

        try:
            EmailIntent(clean)
            return clean
        except ValueError:
            pass

        mapped = _INTENT_ALIASES.get(clean)
        if mapped:
            logger.debug(
                "EmailAgent: mapped boss intent '%s' → '%s'", raw_intent, mapped,
            )
            return mapped

        logger.warning(
            "EmailAgent: unrecognizable boss intent '%s' — ignoring advice",
            raw_intent,
        )
        return None

    @staticmethod
    def build_reputation_context(
        sender_rep: dict[str, Any],
        domain_rep: dict[str, Any],
    ) -> str:
        """Build a reputation context string for the classification prompt."""
        parts = []
        if sender_rep.get("known"):
            parts.append(
                f"Sender: {sender_rep['total']} previous emails, "
                f"{sender_rep['ignore_rate']*100:.0f}% ignored, "
                f"{sender_rep.get('notify_count', 0)} notified, "
                f"{sender_rep.get('reply_count', 0)} replied"
            )
        if domain_rep.get("known"):
            feedback = ""
            neg = domain_rep.get("negative_feedback", 0)
            pos = domain_rep.get("positive_feedback", 0)
            if neg or pos:
                feedback = f", feedback: {pos} positive / {neg} negative"
            parts.append(
                f"Domain ({domain_rep['domain']}): {domain_rep['total']} total emails, "
                f"{domain_rep['ignore_rate']*100:.0f}% ignored{feedback}"
            )
        return "\n".join(parts)

    @staticmethod
    def build_email_signals(headers: dict[str, str]) -> str:
        """Build email signal context from headers for the classification prompt."""
        if not headers:
            return ""
        parts = []
        if "List-Unsubscribe" in headers:
            parts.append("- Has List-Unsubscribe header (likely newsletter/mailing list)")
        if "Precedence" in headers:
            val = headers["Precedence"].lower()
            parts.append(f"- Precedence: {val} (automated/bulk email)")
        if "List-Id" in headers:
            parts.append(f"- List-Id: {headers['List-Id']} (mailing list)")
        if "X-Mailer" in headers:
            parts.append(f"- X-Mailer: {headers['X-Mailer']}")
        return "\n".join(parts)
