"""Tests for permission system."""

import time
import pytest
from unittest.mock import MagicMock

from blick.core.permissions import (
    PermissionAction,
    PermissionChecker,
    PermissionRule,
    PermissionSet,
)


class TestPermissionRule:
    def test_defaults(self):
        rule = PermissionRule(action="comment")
        assert rule.allowed is True
        assert rule.max_per_hour == 0
        assert rule.requires_approval is False
        assert rule.cooldown_seconds == 0

    def test_custom_values(self):
        rule = PermissionRule(
            action="post",
            allowed=True,
            max_per_hour=4,
            requires_approval=True,
            cooldown_seconds=60,
        )
        assert rule.max_per_hour == 4
        assert rule.requires_approval is True


class TestPermissionSet:
    def test_empty_set(self):
        ps = PermissionSet()
        assert ps.get_rule("comment") is None
        assert ps.default_allowed is False  # Security: default deny

    def test_from_dict(self):
        data = {
            "post": {"allowed": True, "max_per_hour": 4},
            "dm": {"allowed": False},
            "comment": {"allowed": True, "max_per_hour": 10, "cooldown_seconds": 30},
        }
        ps = PermissionSet.from_dict(data)
        assert ps.get_rule("post").max_per_hour == 4
        assert ps.get_rule("dm").allowed is False
        assert ps.get_rule("comment").cooldown_seconds == 30
        assert ps.get_rule("nonexistent") is None

    def test_from_dict_bool_shorthand(self):
        data = {"post": True, "dm": False}
        ps = PermissionSet.from_dict(data)
        assert ps.get_rule("post").allowed is True
        assert ps.get_rule("dm").allowed is False

    def test_is_explicitly_denied(self):
        data = {"dm": {"allowed": False}}
        ps = PermissionSet.from_dict(data)
        assert ps.is_explicitly_denied("dm") is True
        assert ps.is_explicitly_denied("comment") is False

    def test_default_not_allowed(self):
        ps = PermissionSet.from_dict({}, default_allowed=False)
        assert ps.default_allowed is False


class TestPermissionChecker:
    def test_denies_by_default(self):
        """Security: default-deny policy — undefined actions are blocked."""
        pc = PermissionChecker(PermissionSet())
        assert pc.is_allowed("anything") is False

    def test_allows_with_explicit_default_allow(self):
        """Explicit opt-in to allow-by-default (legacy support)."""
        ps = PermissionSet(default_allowed=True)
        pc = PermissionChecker(ps)
        assert pc.is_allowed("anything") is True

    def test_denies_by_default_policy(self):
        ps = PermissionSet(default_allowed=False)
        pc = PermissionChecker(ps)
        assert pc.is_allowed("anything") is False

    def test_explicitly_denied(self):
        ps = PermissionSet.from_dict({"dm": {"allowed": False}, "comment": {"allowed": True}})
        pc = PermissionChecker(ps)
        assert pc.is_allowed("dm") is False
        assert pc.is_allowed("comment") is True

    def test_denial_reason_explicit(self):
        ps = PermissionSet.from_dict({"dm": {"allowed": False}})
        pc = PermissionChecker(ps)
        reason = pc.denial_reason("dm")
        assert reason is not None
        assert "denied" in reason.lower()

    def test_denial_reason_allowed(self):
        ps = PermissionSet.from_dict({"comment": {"allowed": True}})
        pc = PermissionChecker(ps)
        assert pc.denial_reason("comment") is None

    def test_rate_limit(self):
        ps = PermissionSet.from_dict({"comment": {"allowed": True, "max_per_hour": 2}})
        pc = PermissionChecker(ps)
        assert pc.is_allowed("comment") is True
        pc.record_action("comment")
        assert pc.is_allowed("comment") is True
        pc.record_action("comment")
        assert pc.is_allowed("comment") is False

    def test_rate_limit_denial_reason(self):
        ps = PermissionSet.from_dict({"comment": {"allowed": True, "max_per_hour": 1}})
        pc = PermissionChecker(ps)
        pc.record_action("comment")
        reason = pc.denial_reason("comment")
        assert reason is not None
        assert "rate limited" in reason.lower()

    def test_cooldown(self):
        ps = PermissionSet.from_dict({"post": {"allowed": True, "cooldown_seconds": 3600}})
        pc = PermissionChecker(ps)
        # First action is fine (no previous action)
        assert pc.is_allowed("post") is True
        pc.record_action("post")
        # Second action blocked by cooldown
        assert pc.is_allowed("post") is False

    def test_requires_approval(self):
        ps = PermissionSet.from_dict({"learn": {"allowed": True, "requires_approval": True}})
        pc = PermissionChecker(ps)
        # Blocked without approval
        assert pc.is_allowed("learn") is False
        reason = pc.denial_reason("learn")
        assert "approval" in reason.lower()
        # Grant approval
        pc.grant_approval("learn")
        assert pc.is_allowed("learn") is True

    def test_approval_consumed_after_action(self):
        ps = PermissionSet.from_dict({"learn": {"allowed": True, "requires_approval": True}})
        pc = PermissionChecker(ps)
        pc.grant_approval("learn")
        pc.record_action("learn")
        # Approval consumed
        assert pc.is_allowed("learn") is False

    def test_from_identity(self):
        identity = MagicMock()
        identity.raw_config = {
            "permissions": {
                "post": {"allowed": True, "max_per_hour": 4},
                "dm": {"allowed": False},
            }
        }
        pc = PermissionChecker.from_identity(identity)
        assert pc.is_allowed("post") is True
        assert pc.is_allowed("dm") is False

    def test_from_identity_no_permissions(self):
        """No permissions config → default deny policy."""
        identity = MagicMock()
        identity.raw_config = {}
        pc = PermissionChecker.from_identity(identity)
        assert pc.is_allowed("anything") is False

    def test_get_stats(self):
        ps = PermissionSet.from_dict({"comment": {"allowed": True, "max_per_hour": 10}})
        pc = PermissionChecker(ps)
        pc.record_action("comment")
        pc.record_action("comment")
        stats = pc.get_stats()
        assert stats["comment"]["actions_this_hour"] == 2
        assert stats["comment"]["max_per_hour"] == 10


class TestPermissionAction:
    def test_enum_values(self):
        assert PermissionAction.POST.value == "post"
        assert PermissionAction.COMMENT.value == "comment"
        assert PermissionAction.DM.value == "dm"
        assert PermissionAction.LEARN.value == "learn"
        assert PermissionAction.DREAM.value == "dream"
