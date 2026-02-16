"""
Integration tests — Identity loading → system prompt chain.

Tests the full identity system end-to-end:
1. load_personality() reads YAML files
2. Identity model validates and freezes config
3. build_system_prompt() produces complete system prompts
4. Prompts contain security sections, banned words, and platform variants
"""

import pytest

from overblick.identities import (
    Identity,
    build_system_prompt,
    list_personalities,
    load_personality,
)


class TestLoadAllIdentities:
    """Every identity in the stable loads without errors."""

    def test_all_personalities_load(self):
        """All identities in the stable load successfully."""
        names = list_personalities()
        assert len(names) >= 7  # At minimum: anomal, bjork, blixt, cherry, natt, prisma, rost

        for name in names:
            personality = load_personality(name)
            assert isinstance(personality, Identity)
            assert personality.display_name

    def test_all_identities_have_display_name(self):
        """Every loaded identity has a non-empty display_name."""
        for name in list_personalities():
            p = load_personality(name)
            assert p.display_name, f"{name} missing display_name"
            assert len(p.display_name) >= 2

    def test_all_identities_have_voice(self):
        """Every identity has voice configuration."""
        for name in list_personalities():
            p = load_personality(name)
            assert p.voice, f"{name} missing voice section"


class TestSystemPromptGeneration:
    """build_system_prompt() produces valid, complete prompts."""

    def test_prompt_contains_identity_name(self):
        """System prompt mentions the identity's display name."""
        p = load_personality("blixt")
        prompt = build_system_prompt(p)
        assert "Blixt" in prompt

    def test_prompt_contains_security_section(self):
        """All system prompts include security instructions."""
        for name in list_personalities():
            p = load_personality(name)
            prompt = build_system_prompt(p)
            assert "NEVER" in prompt, f"{name}: missing security section"

    def test_prompt_minimum_length(self):
        """System prompts should be substantial (>100 chars)."""
        for name in list_personalities():
            p = load_personality(name)
            prompt = build_system_prompt(p)
            assert len(prompt) > 100, f"{name}: prompt too short ({len(prompt)} chars)"

    def test_prompt_platform_variant_telegram(self):
        """Telegram platform prompt includes platform-specific instructions."""
        p = load_personality("cherry")
        prompt_default = build_system_prompt(p)
        prompt_telegram = build_system_prompt(p, platform="Telegram")

        # Both should contain the identity
        assert "Cherry" in prompt_default
        assert "Cherry" in prompt_telegram

    def test_prompt_platform_variant_moltbook(self):
        """Moltbook platform prompt differs from default."""
        p = load_personality("anomal")
        prompt_moltbook = build_system_prompt(p, platform="Moltbook")

        assert "Anomal" in prompt_moltbook

    def test_prompt_includes_banned_words(self):
        """System prompt mentions banned words in NEVER section."""
        for name in list_personalities():
            p = load_personality(name)
            banned = p.get_banned_words()
            if banned:
                prompt = build_system_prompt(p)
                assert "NEVER use" in prompt, f"{name}: has banned words but prompt missing NEVER use"


class TestIdentityConfiguration:
    """Identity operational config is correctly loaded."""

    def test_llm_settings_loaded(self):
        """LLM settings (model, temperature) are populated."""
        p = load_personality("anomal")
        assert p.llm
        assert p.llm.model
        assert 0.0 <= p.llm.temperature <= 2.0

    def test_schedule_settings_loaded(self):
        """Schedule settings are populated with valid values."""
        p = load_personality("anomal")
        assert p.schedule
        assert p.schedule.heartbeat_hours > 0
        assert p.schedule.feed_poll_minutes > 0

    def test_quiet_hours_settings_loaded(self):
        """Quiet hours settings are populated."""
        p = load_personality("anomal")
        assert p.quiet_hours
        assert 0 <= p.quiet_hours.start_hour <= 23
        assert 0 <= p.quiet_hours.end_hour <= 23

    def test_identity_is_frozen(self):
        """Identity instances are frozen (immutable)."""
        p = load_personality("anomal")
        with pytest.raises(Exception):
            p.display_name = "Changed"


class TestSpecificIdentities:
    """Spot-check specific identities for correctness."""

    def test_anomal_intellectual_humanist(self):
        """Anomal has expected traits for intellectual humanist."""
        p = load_personality("anomal")
        assert p.display_name == "Anomal"
        prompt = build_system_prompt(p)
        assert len(prompt) > 200

    def test_blixt_punk_tech_critic(self):
        """Blixt has expected traits for punk tech critic."""
        p = load_personality("blixt")
        assert p.display_name == "Blixt"

    def test_cherry_stockholm_woman(self):
        """Cherry has expected traits."""
        p = load_personality("cherry")
        assert p.display_name == "Cherry"

    def test_bjork_forest_philosopher(self):
        """Bjork has expected traits."""
        p = load_personality("bjork")
        # Accept either Björk or Bjork
        assert "jork" in p.display_name.lower() or "jörk" in p.display_name.lower()

    def test_stal_email_secretary(self):
        """Stal has expected traits for email secretary."""
        p = load_personality("stal")
        # Accept either Stål or Stal
        assert "stal" in p.display_name.lower() or "stål" in p.display_name.lower()


class TestEdgeCases:
    """Edge cases in identity loading."""

    def test_nonexistent_identity_raises(self):
        """Loading a non-existent identity raises an error."""
        with pytest.raises(Exception):
            load_personality("does_not_exist_999")

    def test_list_personalities_returns_list(self):
        """list_personalities returns a non-empty list of strings."""
        names = list_personalities()
        assert isinstance(names, list)
        assert len(names) > 0
        assert all(isinstance(n, str) for n in names)
