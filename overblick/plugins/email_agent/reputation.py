"""
Sender and domain reputation manager for the email agent.

Owns all reputation logic: loading/saving sender profiles (GDPR-safe aggregates),
calculating domain stats, and determining auto-ignore thresholds.
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from overblick.plugins.email_agent.models import EmailClassification, SenderProfile

if TYPE_CHECKING:
    from overblick.plugins.email_agent.database import EmailAgentDB

logger = logging.getLogger(__name__)


class ReputationManager:
    """
    Manages sender and domain reputation for the email agent.

    Profiles are stored as GDPR-safe JSON files containing only aggregate
    statistics (counts, rates, dates) — never email bodies or personal content.
    """

    def __init__(
        self,
        db: Optional["EmailAgentDB"],
        profiles_dir: Optional[Path],
        thresholds: dict,
    ) -> None:
        self._db = db
        self._profiles_dir = profiles_dir
        self._sender_threshold = thresholds.get("sender_ignore_rate", 0.9)
        self._sender_min_count = thresholds.get("sender_min_interactions", 5)
        self._domain_threshold = thresholds.get("domain_ignore_rate", 0.9)
        self._domain_min_count = thresholds.get("domain_min_interactions", 10)

    @staticmethod
    def extract_domain(sender: str) -> str:
        """Extract domain from an email sender string like 'Name <user@domain.com>'."""
        addr = sender
        if "<" in addr and ">" in addr:
            addr = addr[addr.index("<") + 1:addr.index(">")]
        if "@" in addr:
            return addr.split("@", 1)[1].lower().strip()
        return ""

    @staticmethod
    def _safe_sender_name(sender: str) -> str:
        """Create a filesystem-safe filename from an email sender string.

        Strips display name parts, quotes, angle brackets, and replaces
        problematic characters with underscores.
        """
        addr = sender
        if "<" in addr and ">" in addr:
            addr = addr[addr.index("<") + 1:addr.index(">")]
        safe = addr.replace("@", "_at_").replace(".", "_")
        safe = re.sub(r'[<>:"/\\|?*\'"&()!,\s]', '_', safe)
        safe = re.sub(r'_+', '_', safe).strip('_')
        return safe[:200]

    async def load_sender_profile(self, sender: str) -> SenderProfile:
        """Load a sender profile from disk, or create a new one."""
        if not self._profiles_dir:
            return SenderProfile(email=sender)

        safe_name = self._safe_sender_name(sender)
        profile_path = self._profiles_dir / f"{safe_name}.json"

        if profile_path.exists():
            try:
                text = await asyncio.to_thread(profile_path.read_text)
                data = json.loads(text)
                return SenderProfile(**data)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("EmailAgent: failed to load sender profile: %s", e)

        return SenderProfile(email=sender)

    async def save_sender_profile(self, sender: str, profile: SenderProfile) -> None:
        """Persist a sender profile to disk."""
        if not self._profiles_dir:
            return
        safe_name = self._safe_sender_name(sender)
        profile_path = self._profiles_dir / f"{safe_name}.json"
        try:
            data = json.dumps(profile.model_dump(), indent=2)
            await asyncio.to_thread(profile_path.write_text, data)
        except Exception as e:
            logger.error(
                "EmailAgent: failed to save sender profile for %s: %s", sender, e, exc_info=True,
            )

    async def get_sender_reputation(self, sender: str) -> dict[str, Any]:
        """Calculate reputation for a specific sender from profile data."""
        profile = await self.load_sender_profile(sender)
        if profile.total_interactions == 0:
            return {"known": False}

        total = profile.total_interactions
        ignore_count = profile.intent_distribution.get("ignore", 0)
        notify_count = profile.intent_distribution.get("notify", 0)
        reply_count = profile.intent_distribution.get("reply", 0)
        ignore_rate = ignore_count / total if total > 0 else 0.0

        return {
            "known": True,
            "total": total,
            "ignore_rate": round(ignore_rate, 2),
            "ignore_count": ignore_count,
            "notify_count": notify_count,
            "reply_count": reply_count,
            "avg_confidence": round(profile.avg_confidence, 2),
        }

    async def get_domain_reputation(self, sender: str) -> dict[str, Any]:
        """Get aggregate reputation for a sender's domain from DB."""
        domain = self.extract_domain(sender)
        if not domain or not self._db:
            return {"known": False}

        stats = await self._db.get_domain_stats(domain)
        if not stats:
            return {"known": False, "domain": domain}

        total = (
            stats["ignore_count"]
            + stats["notify_count"]
            + stats["reply_count"]
        )
        if total == 0:
            return {"known": False, "domain": domain}

        ignore_rate = stats["ignore_count"] / total

        return {
            "known": True,
            "domain": domain,
            "total": total,
            "ignore_rate": round(ignore_rate, 2),
            "ignore_count": stats["ignore_count"],
            "notify_count": stats["notify_count"],
            "reply_count": stats["reply_count"],
            "negative_feedback": stats["negative_feedback_count"],
            "positive_feedback": stats["positive_feedback_count"],
            "auto_ignore": bool(stats["auto_ignore"]),
        }

    def should_auto_ignore_sender(self, reputation: dict[str, Any]) -> bool:
        """Check if sender should be auto-ignored based on learned reputation."""
        if not reputation.get("known"):
            return False
        total = reputation.get("total", 0)
        ignore_rate = reputation.get("ignore_rate", 0.0)
        return (
            total >= self._sender_min_count
            and ignore_rate >= self._sender_threshold
        )

    def should_auto_ignore_domain(self, reputation: dict[str, Any]) -> bool:
        """Check if domain should be auto-ignored based on learned reputation."""
        if not reputation.get("known"):
            return False
        if reputation.get("auto_ignore"):
            return True
        total = reputation.get("total", 0)
        ignore_rate = reputation.get("ignore_rate", 0.0)
        positive = reputation.get("positive_feedback", 0)
        return (
            total >= self._domain_min_count
            and ignore_rate >= self._domain_threshold
            and positive == 0
        )

    async def update_sender_profile(
        self, sender: str, classification: EmailClassification,
    ) -> None:
        """
        Update the consolidated sender profile after each conversation.

        Profile files contain GDPR-safe aggregate data only:
        interaction counts, language preference, intent distribution.
        No email bodies, no personal content.
        """
        profile = await self.load_sender_profile(sender)

        profile.total_interactions += 1
        profile.last_interaction_date = datetime.now().strftime("%Y-%m-%d")
        profile.avg_confidence = (
            (profile.avg_confidence * (profile.total_interactions - 1) + classification.confidence)
            / profile.total_interactions
        )

        intent = classification.intent.value
        profile.intent_distribution[intent] = profile.intent_distribution.get(intent, 0) + 1

        await self.save_sender_profile(sender, profile)

    async def penalize_sender(self, sender: str) -> None:
        """
        Increment the ignore count for a sender based on negative feedback.

        Unlike update_sender_profile(), this does NOT increment total_interactions —
        it is called on negative feedback to tilt the reputation without double-counting.
        """
        profile = await self.load_sender_profile(sender)
        profile.intent_distribution["ignore"] = (
            profile.intent_distribution.get("ignore", 0) + 1
        )
        await self.save_sender_profile(sender, profile)
