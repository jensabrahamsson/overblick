"""Tests for dashboard personality service."""

from unittest.mock import MagicMock, patch

import pytest

from overblick.dashboard.services.personality import PersonalityService


class TestPersonalityService:
    @pytest.fixture
    def svc(self):
        return PersonalityService()

    def test_should_list_identities_when_called(self, svc):
        with patch("overblick.identities.list_identities", return_value=["anomal"]):
            result = svc.list_identities()
            assert result == ["anomal"]

    def test_should_return_personality_dict_when_found(self, svc):
        mock_p = MagicMock()
        mock_p.name = "anomal"
        mock_p.display_name = "Anomal"
        mock_p.version = "1.0"
        mock_p.identity_info = {"age": "30s"}
        mock_p.backstory = {"origin": "sweden"}
        mock_p.voice = {"base_tone": "warm"}
        mock_p.traits = {"openness": 0.9}
        mock_p.interests = {"tech": True}
        mock_p.vocabulary = {"level": "academic"}
        mock_p.signature_phrases = {"greeting": ["hej"]}
        mock_p.ethos = ["be kind"]
        mock_p.moltbook_bio = "A thinker"
        mock_p.raw = {"extra": "data"}

        with patch("overblick.identities.load_identity", return_value=mock_p):
            result = svc.get_personality("anomal")

        assert result is not None
        assert result["name"] == "anomal"
        assert result["display_name"] == "Anomal"
        assert result["version"] == "1.0"
        assert result["identity_info"] == {"age": "30s"}
        assert result["backstory"] == {"origin": "sweden"}
        assert result["voice"] == {"base_tone": "warm"}
        assert result["traits"] == {"openness": 0.9}
        assert result["interests"] == {"tech": True}
        assert result["vocabulary"] == {"level": "academic"}
        assert result["signature_phrases"] == {"greeting": ["hej"]}
        assert result["ethos"] == ["be kind"]
        assert result["moltbook_bio"] == "A thinker"
        assert result["raw"] == {"extra": "data"}

    def test_should_return_dict_ethos_when_ethos_is_dict(self, svc):
        mock_p = MagicMock()
        mock_p.name = "test"
        mock_p.display_name = "Test"
        mock_p.version = "1.0"
        mock_p.identity_info = {}
        mock_p.backstory = {}
        mock_p.voice = {}
        mock_p.traits = {}
        mock_p.interests = {}
        mock_p.vocabulary = {}
        mock_p.signature_phrases = {}
        mock_p.ethos = {"principle": "honesty"}
        mock_p.moltbook_bio = ""
        mock_p.raw = None

        with patch("overblick.identities.load_identity", return_value=mock_p):
            result = svc.get_personality("test")

        assert result["ethos"] == {"principle": "honesty"}
        assert result["raw"] == {}

    def test_should_return_empty_ethos_when_ethos_is_none(self, svc):
        mock_p = MagicMock()
        mock_p.name = "test"
        mock_p.display_name = "Test"
        mock_p.version = "1.0"
        mock_p.identity_info = {}
        mock_p.backstory = {}
        mock_p.voice = {}
        mock_p.traits = {}
        mock_p.interests = {}
        mock_p.vocabulary = {}
        mock_p.signature_phrases = {}
        mock_p.ethos = None
        mock_p.moltbook_bio = ""
        mock_p.raw = {}

        with patch("overblick.identities.load_identity", return_value=mock_p):
            result = svc.get_personality("test")

        assert result["ethos"] == {}

    def test_should_return_none_when_identity_not_found(self, svc):
        with patch("overblick.identities.load_identity", side_effect=FileNotFoundError):
            result = svc.get_personality("missing")
        assert result is None

    def test_should_return_none_when_generic_error(self, svc):
        with patch("overblick.identities.load_identity", side_effect=RuntimeError("boom")):
            result = svc.get_personality("broken")
        assert result is None

    def test_should_return_all_personalities_when_called(self, svc):
        mock_p = MagicMock()
        mock_p.name = "anomal"
        mock_p.display_name = "Anomal"
        mock_p.version = "1.0"
        mock_p.identity_info = {}
        mock_p.backstory = {}
        mock_p.voice = {}
        mock_p.traits = {}
        mock_p.interests = {}
        mock_p.vocabulary = {}
        mock_p.signature_phrases = {}
        mock_p.ethos = []
        mock_p.moltbook_bio = ""
        mock_p.raw = {}

        with patch("overblick.identities.list_identities", return_value=["anomal"]), \
             patch("overblick.identities.load_identity", return_value=mock_p):
            result = svc.get_all_personalities()

        assert len(result) == 1
        assert result[0]["name"] == "anomal"

    def test_should_skip_failed_in_get_all_personalities(self, svc):
        with patch("overblick.identities.list_identities", return_value=["bad"]), \
             patch("overblick.identities.load_identity", side_effect=FileNotFoundError):
            result = svc.get_all_personalities()
        assert result == []
