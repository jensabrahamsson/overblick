"""
Permission system — declarative action control per identity.

Each identity can define which actions it's allowed to perform,
with per-action rate limits and optional boss-agent approval.

Permissions are defined in identity YAML:

    permissions:
      post:
        allowed: true
        max_per_hour: 4
      comment:
        allowed: true
        max_per_hour: 10
      dm:
        allowed: false
      learn:
        allowed: true
        requires_approval: true

Usage:
    perms = PermissionChecker.from_identity(identity)
    if perms.is_allowed("comment"):
        post_comment(...)
    else:
        reason = perms.denial_reason("comment")
"""

import logging
import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class PermissionAction(Enum):
    """Standard agent actions that can be permission-controlled."""
    POST = "post"
    COMMENT = "comment"
    REPLY = "reply"
    UPVOTE = "upvote"
    DM = "dm"
    LEARN = "learn"
    DREAM = "dream"
    HEARTBEAT = "heartbeat"
    THERAPY = "therapy"
    API_CALL = "api_call"


class PermissionRule(BaseModel):
    """
    Single permission rule for an action.

    Attributes:
        action: The action this rule controls
        allowed: Whether the action is permitted at all
        max_per_hour: Rate limit (0 = unlimited)
        requires_approval: Whether boss-agent approval is needed
        cooldown_seconds: Minimum time between consecutive actions
    """
    model_config = ConfigDict(frozen=True)

    action: str
    allowed: bool = True
    max_per_hour: int = 0
    requires_approval: bool = False
    cooldown_seconds: int = 0


class PermissionSet(BaseModel):
    """
    Complete permission configuration for an identity.

    Holds all permission rules and provides lookup.
    """
    rules: dict[str, PermissionRule] = {}

    # Default policy when an action has no explicit rule.
    # SECURITY: Default deny — actions must be explicitly permitted.
    default_allowed: bool = False

    def get_rule(self, action: str) -> Optional[PermissionRule]:
        """Get the rule for an action, or None if no explicit rule."""
        return self.rules.get(action)

    def is_explicitly_denied(self, action: str) -> bool:
        """Check if an action is explicitly set to not allowed."""
        rule = self.rules.get(action)
        return rule is not None and not rule.allowed

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        default_allowed: bool = False,
    ) -> "PermissionSet":
        """
        Build PermissionSet from a dict (typically from YAML).

        Expected format:
            {
                "post": {"allowed": true, "max_per_hour": 4},
                "dm": {"allowed": false},
            }
        """
        rules = {}
        for action_name, config in data.items():
            if isinstance(config, dict):
                rules[action_name] = PermissionRule(
                    action=action_name,
                    allowed=config.get("allowed", True),
                    max_per_hour=config.get("max_per_hour", 0),
                    requires_approval=config.get("requires_approval", False),
                    cooldown_seconds=config.get("cooldown_seconds", 0),
                )
            elif isinstance(config, bool):
                rules[action_name] = PermissionRule(
                    action=action_name,
                    allowed=config,
                )
        return cls(rules=rules, default_allowed=default_allowed)


class _ActionTracker(BaseModel):
    """Tracks action invocations for rate limiting."""
    timestamps: list[float] = []
    last_action: float = 0.0


class PermissionChecker:
    """
    Runtime permission checker with rate limiting.

    Evaluates whether an action is allowed based on:
    1. Static permission rules (allowed/denied)
    2. Per-hour rate limits
    3. Cooldown periods
    4. Approval requirements (tracked but not enforced here)
    """

    def __init__(self, permission_set: PermissionSet):
        self._perms = permission_set
        self._trackers: dict[str, _ActionTracker] = {}
        self._pending_approvals: set[str] = set()

    @classmethod
    def from_identity(cls, identity: Any) -> "PermissionChecker":
        """
        Create a PermissionChecker from an Identity object.

        Reads permissions from identity.raw_config["permissions"].
        """
        raw = {}
        if hasattr(identity, "raw_config") and isinstance(identity.raw_config, dict):
            raw = identity.raw_config.get("permissions", {})
        perm_set = PermissionSet.from_dict(raw) if raw else PermissionSet()
        return cls(perm_set)

    def is_allowed(self, action: str) -> bool:
        """
        Check if an action is currently allowed.

        Considers: static rules, rate limits, cooldowns.
        Does NOT consume a rate limit token — call record_action() after.

        Args:
            action: Action name (e.g. "comment", "post")

        Returns:
            True if the action is allowed right now
        """
        rule = self._perms.get_rule(action)

        # No explicit rule — use default policy
        if rule is None:
            return self._perms.default_allowed

        # Explicitly denied
        if not rule.allowed:
            return False

        # Requires approval and not yet approved
        if rule.requires_approval and action not in self._pending_approvals:
            return False

        # Rate limit check
        if rule.max_per_hour > 0:
            tracker = self._get_tracker(action)
            self._prune_old_timestamps(tracker)
            if len(tracker.timestamps) >= rule.max_per_hour:
                return False

        # Cooldown check
        if rule.cooldown_seconds > 0:
            tracker = self._get_tracker(action)
            if tracker.last_action > 0:
                elapsed = time.monotonic() - tracker.last_action
                if elapsed < rule.cooldown_seconds:
                    return False

        return True

    def record_action(self, action: str) -> None:
        """
        Record that an action was performed (for rate limiting).

        Call this AFTER successfully performing the action.
        """
        tracker = self._get_tracker(action)
        now = time.monotonic()
        tracker.timestamps.append(now)
        tracker.last_action = now

        # Remove approval after use
        self._pending_approvals.discard(action)

    def grant_approval(self, action: str) -> None:
        """
        Grant one-time approval for an action that requires_approval.

        Used by boss-agent or admin to authorize a single action.
        """
        self._pending_approvals.add(action)

    def denial_reason(self, action: str) -> Optional[str]:
        """
        Get a human-readable reason why an action is denied.

        Returns None if the action is allowed.
        """
        rule = self._perms.get_rule(action)

        if rule is None:
            if not self._perms.default_allowed:
                return f"Action '{action}' denied by default policy"
            return None

        if not rule.allowed:
            return f"Action '{action}' is explicitly denied"

        if rule.requires_approval and action not in self._pending_approvals:
            return f"Action '{action}' requires boss approval"

        if rule.max_per_hour > 0:
            tracker = self._get_tracker(action)
            self._prune_old_timestamps(tracker)
            if len(tracker.timestamps) >= rule.max_per_hour:
                return f"Action '{action}' rate limited ({rule.max_per_hour}/hour)"

        if rule.cooldown_seconds > 0:
            tracker = self._get_tracker(action)
            if tracker.last_action > 0:
                elapsed = time.monotonic() - tracker.last_action
                remaining = rule.cooldown_seconds - elapsed
                if remaining > 0:
                    return f"Action '{action}' on cooldown ({remaining:.0f}s remaining)"

        return None

    def get_stats(self) -> dict[str, dict[str, Any]]:
        """Get permission stats for all tracked actions."""
        stats = {}
        for action, tracker in self._trackers.items():
            self._prune_old_timestamps(tracker)
            rule = self._perms.get_rule(action)
            stats[action] = {
                "actions_this_hour": len(tracker.timestamps),
                "max_per_hour": rule.max_per_hour if rule else 0,
                "allowed": self.is_allowed(action),
            }
        return stats

    def _get_tracker(self, action: str) -> _ActionTracker:
        """Get or create tracker for an action."""
        if action not in self._trackers:
            self._trackers[action] = _ActionTracker()
        return self._trackers[action]

    def _prune_old_timestamps(self, tracker: _ActionTracker) -> None:
        """Remove timestamps older than 1 hour."""
        cutoff = time.monotonic() - 3600
        tracker.timestamps = [t for t in tracker.timestamps if t > cutoff]
