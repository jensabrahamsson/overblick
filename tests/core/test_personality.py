"""Tests for personality system."""

import pytest
from pathlib import Path

import yaml
from pydantic import ValidationError

from overblick.personalities import (
    Personality,
    load_personality,
    list_personalities,
    _build_personality,
)


class TestPersonality:
    def test_default_values(self):
        p = Personality(name="test")
        assert p.name == "test"
        assert p.display_name == "Test"  # Auto-capitalized from name
        assert p.voice == {}
        assert p.traits == {}
        assert p.interests == {}

    def test_get_trait(self):
        p = Personality(name="test", traits={"openness": 0.9, "warmth": 0.6})
        assert p.get_trait("openness") == 0.9
        assert p.get_trait("warmth") == 0.6
        assert p.get_trait("missing") == 0.5  # default
        assert p.get_trait("missing", 0.3) == 0.3

    def test_get_example(self):
        examples = {
            "greeting": {"user_message": "Hi", "anomal_response": "Hello"},
        }
        p = Personality(name="test", examples=examples)
        assert p.get_example("greeting") is not None
        assert p.get_example("nonexistent") is None

    def test_get_banned_words(self):
        vocab = {"banned_words": ["fren", "wagmi"], "preferred_words": ["rather"]}
        p = Personality(name="test", vocabulary=vocab)
        assert p.get_banned_words() == ["fren", "wagmi"]
        assert p.get_preferred_words() == ["rather"]

    def test_get_banned_words_empty(self):
        p = Personality(name="test")
        assert p.get_banned_words() == []

    def test_get_interest_topics(self):
        interests = {
            "crypto": {"enthusiasm_level": "expert", "topics": ["DeFi", "NFTs"]},
        }
        p = Personality(name="test", interests=interests)
        assert p.get_interest_topics("crypto") == ["DeFi", "NFTs"]
        assert p.get_interest_topics("unknown") == []

    def test_frozen(self):
        p = Personality(name="test")
        with pytest.raises(ValidationError):
            p.name = "other"


class TestBuildPersonality:
    def test_basic_build(self):
        data = {
            "identity": {
                "name": "TestAgent",
                "display_name": "Test Agent",
                "version": "2.0",
            },
            "voice": {"base_tone": "friendly"},
            "traits": {"openness": 0.8},
            "backstory": {"origin": "Testing lab"},
            "moltbook_bio": "I am a test agent.",
        }
        p = _build_personality("test", data)
        assert p.name == "test"
        assert p.display_name == "Test Agent"
        assert p.version == "2.0"
        assert p.voice["base_tone"] == "friendly"
        assert p.traits["openness"] == 0.8
        assert p.moltbook_bio == "I am a test agent."

    def test_empty_data(self):
        p = _build_personality("empty", {})
        assert p.name == "empty"
        assert p.display_name == "Empty"
        assert p.voice == {}

    def test_raw_preserved(self):
        data = {"custom_key": "custom_value", "traits": {"x": 1}}
        p = _build_personality("test", data)
        assert p.raw["custom_key"] == "custom_value"


class TestLoadPersonality:
    def test_load_from_identity_dir(self):
        """Load personality from existing identity directory."""
        p = load_personality("anomal")
        assert p.name == "anomal"
        assert p.voice != {}
        assert "base_tone" in p.voice
        assert p.traits != {}
        assert "openness" in p.traits

    def test_load_nonexistent(self):
        with pytest.raises(FileNotFoundError):
            load_personality("nonexistent_agent_xyz")

    def test_load_from_standalone(self, tmp_path, monkeypatch):
        """Test loading from standalone personality file."""
        import overblick.personalities as personalities_mod

        # Create a standalone personality file
        standalone_data = {
            "identity": {"display_name": "Standalone"},
            "voice": {"base_tone": "casual"},
            "traits": {"humor": 0.9},
        }
        standalone_file = tmp_path / "standalone.yaml"
        with open(standalone_file, "w") as f:
            yaml.dump(standalone_data, f)

        # Monkeypatch the personalities dir
        monkeypatch.setattr(personalities_mod, "_PERSONALITIES_DIR", tmp_path)

        p = load_personality("standalone")
        assert p.display_name == "Standalone"
        assert p.voice["base_tone"] == "casual"


class TestLoadFromDirectory(object):
    def test_load_from_directory_based(self, tmp_path, monkeypatch):
        """Test loading from directory-based personality."""
        import overblick.personalities as personalities_mod

        # Create directory-based personality
        personality_dir = tmp_path / "dirtest"
        personality_dir.mkdir()
        data = {
            "identity": {"display_name": "DirTest"},
            "voice": {"base_tone": "formal"},
            "traits": {"warmth": 0.3},
        }
        with open(personality_dir / "personality.yaml", "w") as f:
            yaml.dump(data, f)

        monkeypatch.setattr(personalities_mod, "_PERSONALITIES_DIR", tmp_path)

        p = load_personality("dirtest")
        assert p.display_name == "DirTest"
        assert p.voice["base_tone"] == "formal"


class TestListPersonalities:
    def test_lists_identities(self):
        names = list_personalities()
        assert "anomal" in names
        assert "cherry" in names


class TestIdentityPersonalityWiring:
    """Verify that load_identity() automatically loads personality."""

    def test_identity_has_loaded_personality(self):
        from overblick.core.identity import load_identity
        identity = load_identity("anomal")
        assert identity.loaded_personality is not None
        assert identity.loaded_personality.name == "anomal"

    def test_identity_personality_ref(self):
        from overblick.core.identity import load_identity
        identity = load_identity("anomal")
        assert identity.personality_ref == "anomal"

    def test_identity_personality_has_voice(self):
        from overblick.core.identity import load_identity
        identity = load_identity("anomal")
        assert identity.loaded_personality.voice != {}
        assert "base_tone" in identity.loaded_personality.voice

    def test_identity_personality_has_traits(self):
        from overblick.core.identity import load_identity
        identity = load_identity("anomal")
        assert identity.loaded_personality.traits != {}
        assert "openness" in identity.loaded_personality.traits

    def test_cherry_has_personality(self):
        from overblick.core.identity import load_identity
        identity = load_identity("cherry")
        assert identity.loaded_personality is not None
        assert identity.loaded_personality.name == "cherry"
