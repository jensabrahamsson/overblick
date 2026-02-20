"""
Unit tests for ReputationManager — isolated from plugin lifecycle.

Tests cover:
- Domain extraction (static)
- Filename sanitization (static)
- Sender and domain reputation calculation
- Auto-ignore thresholds
- Sender profile load/save/update
- penalize_sender (negative feedback)
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.plugins.email_agent.models import EmailClassification, EmailIntent, SenderProfile
from overblick.plugins.email_agent.reputation import ReputationManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS = {
    "sender_ignore_rate": 0.9,
    "sender_min_interactions": 5,
    "domain_ignore_rate": 0.9,
    "domain_min_interactions": 10,
}


def make_reputation(tmp_path: Path, db=None, thresholds=None):
    """Create a ReputationManager with a temp profiles_dir."""
    profiles_dir = tmp_path / "sender_profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    return ReputationManager(
        db=db or MagicMock(),
        profiles_dir=profiles_dir,
        thresholds=thresholds or DEFAULT_THRESHOLDS,
    )


# ---------------------------------------------------------------------------
# extract_domain — static
# ---------------------------------------------------------------------------

class TestExtractDomain:
    """Tests for ReputationManager.extract_domain()."""

    def test_simple_email(self):
        """Extracts domain from plain email address."""
        assert ReputationManager.extract_domain("user@example.com") == "example.com"

    def test_name_angle_brackets(self):
        """Extracts domain from 'Name <user@domain>' format."""
        assert ReputationManager.extract_domain("Alice <alice@acme.org>") == "acme.org"

    def test_uppercase_normalized(self):
        """Domain is lowercased."""
        assert ReputationManager.extract_domain("user@EXAMPLE.COM") == "example.com"

    def test_no_at_sign(self):
        """Returns empty string when there is no @ sign."""
        assert ReputationManager.extract_domain("invalid-sender") == ""

    def test_subdomain(self):
        """Preserves subdomain in result."""
        assert ReputationManager.extract_domain("user@mail.example.co.uk") == "mail.example.co.uk"


# ---------------------------------------------------------------------------
# _safe_sender_name — static
# ---------------------------------------------------------------------------

class TestSafeSenderName:
    """Tests for ReputationManager._safe_sender_name()."""

    def test_simple_email(self):
        """Simple email → clean underscore-delimited filename."""
        assert ReputationManager._safe_sender_name("user@example.com") == "user_at_example_com"

    def test_display_name_stripped(self):
        """Display name and angle brackets are stripped."""
        assert ReputationManager._safe_sender_name("Alice <alice@acme.org>") == "alice_at_acme_org"

    def test_complex_special_chars(self):
        """Complex display names with quotes and special chars are sanitised."""
        result = ReputationManager._safe_sender_name(
            '"Adam from S&P 500" <trade@substack.com>'
        )
        assert result == "trade_at_substack_com"

    def test_no_filesystem_dangerous_chars(self):
        """Result never contains characters dangerous for filesystems."""
        result = ReputationManager._safe_sender_name('Test "Quoted" <user/path@domain.com>')
        for ch in ('/', '"', "'", '\\', '<', '>'):
            assert ch not in result

    def test_max_length(self):
        """Result is capped at 200 characters."""
        long_sender = "a" * 300 + "@example.com"
        result = ReputationManager._safe_sender_name(long_sender)
        assert len(result) <= 200


# ---------------------------------------------------------------------------
# Sender reputation
# ---------------------------------------------------------------------------

class TestSenderReputation:
    """Tests for get_sender_reputation() and should_auto_ignore_sender()."""

    @pytest.mark.asyncio
    async def test_unknown_sender_returns_known_false(self, tmp_path):
        """Unknown sender (no profile on disk) returns known=False."""
        rep = make_reputation(tmp_path)
        result = await rep.get_sender_reputation("new@example.com")
        assert result == {"known": False}

    @pytest.mark.asyncio
    async def test_known_sender_reputation_calculated(self, tmp_path):
        """Known sender with profile returns correct stats."""
        rep = make_reputation(tmp_path)

        profile = SenderProfile(
            email="news@spam.com",
            total_interactions=10,
            intent_distribution={"ignore": 9, "notify": 1},
            avg_confidence=0.85,
        )
        safe_name = ReputationManager._safe_sender_name("news@spam.com")
        (tmp_path / "sender_profiles" / f"{safe_name}.json").write_text(
            json.dumps(profile.model_dump(), indent=2)
        )

        result = await rep.get_sender_reputation("news@spam.com")
        assert result["known"] is True
        assert result["total"] == 10
        assert result["ignore_rate"] == 0.9
        assert result["ignore_count"] == 9
        assert result["notify_count"] == 1

    def test_should_auto_ignore_true_high_rate(self, tmp_path):
        """Auto-ignore triggered when rate >= threshold and count >= min."""
        rep = make_reputation(tmp_path)
        assert rep.should_auto_ignore_sender({"known": True, "total": 10, "ignore_rate": 0.95}) is True

    def test_should_auto_ignore_false_low_count(self, tmp_path):
        """Auto-ignore NOT triggered when interaction count is too low."""
        rep = make_reputation(tmp_path)
        assert rep.should_auto_ignore_sender({"known": True, "total": 3, "ignore_rate": 1.0}) is False

    def test_should_auto_ignore_false_low_rate(self, tmp_path):
        """Auto-ignore NOT triggered when ignore rate is below threshold."""
        rep = make_reputation(tmp_path)
        assert rep.should_auto_ignore_sender({"known": True, "total": 10, "ignore_rate": 0.5}) is False

    def test_should_auto_ignore_false_unknown(self, tmp_path):
        """Auto-ignore NOT triggered for unknown senders."""
        rep = make_reputation(tmp_path)
        assert rep.should_auto_ignore_sender({"known": False}) is False


# ---------------------------------------------------------------------------
# Domain reputation
# ---------------------------------------------------------------------------

class TestDomainReputation:
    """Tests for get_domain_reputation() and should_auto_ignore_domain()."""

    @pytest.mark.asyncio
    async def test_unknown_domain_returns_known_false(self, tmp_path):
        """Domain with no DB entry returns known=False."""
        db = MagicMock()
        db.get_domain_stats = AsyncMock(return_value=None)
        rep = make_reputation(tmp_path, db=db)
        result = await rep.get_domain_reputation("user@newdomain.com")
        assert result["known"] is False

    @pytest.mark.asyncio
    async def test_known_domain_stats_returned(self, tmp_path):
        """Known domain returns calculated stats."""
        db = MagicMock()
        db.get_domain_stats = AsyncMock(return_value={
            "ignore_count": 45, "notify_count": 5, "reply_count": 0,
            "negative_feedback_count": 2, "positive_feedback_count": 0,
            "auto_ignore": False,
        })
        rep = make_reputation(tmp_path, db=db)
        result = await rep.get_domain_reputation("user@spam.com")
        assert result["known"] is True
        assert result["total"] == 50
        assert result["ignore_rate"] == 0.9

    def test_should_auto_ignore_domain_via_flag(self, tmp_path):
        """Domain with auto_ignore=True is always ignored regardless of rate."""
        rep = make_reputation(tmp_path)
        rep_data = {"known": True, "total": 5, "ignore_rate": 0.2, "positive_feedback": 0, "auto_ignore": True}
        assert rep.should_auto_ignore_domain(rep_data) is True

    def test_should_auto_ignore_domain_false_has_positive_feedback(self, tmp_path):
        """Domain with positive feedback is NOT auto-ignored even at high rate."""
        rep = make_reputation(tmp_path)
        rep_data = {
            "known": True, "total": 15, "ignore_rate": 0.93,
            "positive_feedback": 1, "auto_ignore": False,
        }
        assert rep.should_auto_ignore_domain(rep_data) is False


# ---------------------------------------------------------------------------
# Sender profile load/save/update
# ---------------------------------------------------------------------------

class TestSenderProfilePersistence:
    """Tests for load_sender_profile, save_sender_profile, update_sender_profile."""

    @pytest.mark.asyncio
    async def test_load_creates_new_profile_if_missing(self, tmp_path):
        """Loading a profile that doesn't exist returns a blank SenderProfile."""
        rep = make_reputation(tmp_path)
        profile = await rep.load_sender_profile("new@example.com")
        assert profile.email == "new@example.com"
        assert profile.total_interactions == 0

    @pytest.mark.asyncio
    async def test_save_and_reload(self, tmp_path):
        """Saved profile can be reloaded with correct values."""
        rep = make_reputation(tmp_path)
        profile = SenderProfile(
            email="alice@example.com",
            total_interactions=3,
            intent_distribution={"reply": 3},
            avg_confidence=0.9,
        )
        await rep.save_sender_profile("alice@example.com", profile)
        loaded = await rep.load_sender_profile("alice@example.com")
        assert loaded.total_interactions == 3
        assert loaded.intent_distribution == {"reply": 3}

    @pytest.mark.asyncio
    async def test_update_sender_profile_increments_counts(self, tmp_path):
        """update_sender_profile() increments interaction count and intent."""
        rep = make_reputation(tmp_path)
        classification = EmailClassification(
            intent=EmailIntent.IGNORE, confidence=0.85, reasoning="Spam",
        )
        await rep.update_sender_profile("spam@example.com", classification)
        profile = await rep.load_sender_profile("spam@example.com")
        assert profile.total_interactions == 1
        assert profile.intent_distribution["ignore"] == 1

    @pytest.mark.asyncio
    async def test_update_accumulates_over_multiple_calls(self, tmp_path):
        """Multiple updates accumulate correctly."""
        rep = make_reputation(tmp_path)
        for _ in range(3):
            await rep.update_sender_profile(
                "contact@example.com",
                EmailClassification(intent=EmailIntent.REPLY, confidence=0.9, reasoning="OK"),
            )
        profile = await rep.load_sender_profile("contact@example.com")
        assert profile.total_interactions == 3
        assert profile.intent_distribution["reply"] == 3


# ---------------------------------------------------------------------------
# penalize_sender
# ---------------------------------------------------------------------------

class TestPenalizeSender:
    """Tests for ReputationManager.penalize_sender()."""

    @pytest.mark.asyncio
    async def test_penalize_increments_ignore_count(self, tmp_path):
        """penalize_sender() increments ignore count without changing total_interactions."""
        rep = make_reputation(tmp_path)

        # Establish a baseline profile
        profile = SenderProfile(
            email="user@example.com",
            total_interactions=5,
            intent_distribution={"notify": 5},
            avg_confidence=0.8,
        )
        await rep.save_sender_profile("user@example.com", profile)

        await rep.penalize_sender("user@example.com")

        loaded = await rep.load_sender_profile("user@example.com")
        assert loaded.total_interactions == 5  # unchanged
        assert loaded.intent_distribution.get("ignore", 0) == 1

    @pytest.mark.asyncio
    async def test_penalize_new_sender_creates_profile(self, tmp_path):
        """penalize_sender() works for senders with no existing profile."""
        rep = make_reputation(tmp_path)
        await rep.penalize_sender("unknown@example.com")
        profile = await rep.load_sender_profile("unknown@example.com")
        assert profile.intent_distribution.get("ignore", 0) == 1
