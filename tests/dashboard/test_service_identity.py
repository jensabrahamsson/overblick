"""Tests for dashboard identity service."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overblick.dashboard.services.identity import IdentityService


class TestIdentityService:
    @pytest.fixture
    def svc(self, tmp_path):
        return IdentityService(tmp_path)

    def test_should_list_identities_when_called(self, svc):
        with patch("overblick.identities.list_identities", return_value=["anomal", "cherry"]):
            result = svc.list_identities()
            assert result == ["anomal", "cherry"]

    def test_should_return_identity_dict_when_identity_found(self, svc):
        mock_identity = MagicMock()
        mock_identity.name = "anomal"
        mock_identity.display_name = "Anomal"
        mock_identity.description = "Test identity"
        mock_identity.version = "1.0.0"
        mock_identity.engagement_threshold = 0.5
        mock_identity.plugins = ["moltbook"]
        mock_identity.capability_names = ["knowledge"]
        mock_identity.traits = {"openness": 0.9}
        mock_identity.llm.model = "qwen3:8b"
        mock_identity.llm.temperature = 0.7
        mock_identity.llm.max_tokens = 2000
        mock_identity.quiet_hours.enabled = True
        mock_identity.quiet_hours.timezone = "Europe/Stockholm"
        mock_identity.quiet_hours.start_hour = 21
        mock_identity.quiet_hours.end_hour = 7
        mock_identity.schedule.heartbeat_hours = 4
        mock_identity.schedule.feed_poll_minutes = 5
        mock_identity.security.enable_preflight = True
        mock_identity.security.enable_output_safety = True
        mock_identity.identity_ref = "anomal"

        with patch("overblick.identities.load_identity", return_value=mock_identity):
            result = svc.get_identity("anomal")

        assert result is not None
        assert result["name"] == "anomal"
        assert result["display_name"] == "Anomal"
        assert result["description"] == "Test identity"
        assert result["version"] == "1.0.0"
        assert result["engagement_threshold"] == 0.5
        assert result["plugins"] == ["moltbook"]
        assert result["capability_names"] == ["knowledge"]
        assert result["traits"] == {"openness": 0.9}
        assert result["llm"]["model"] == "qwen3:8b"
        assert result["llm"]["provider"] == "gateway"
        assert result["quiet_hours"]["enabled"] is True
        assert result["schedule"]["heartbeat_hours"] == 4
        assert result["security"]["enable_preflight"] is True
        assert result["identity_ref"] == "anomal"

    def test_should_return_none_when_identity_not_found(self, svc):
        with patch("overblick.identities.load_identity", side_effect=FileNotFoundError("not found")):
            result = svc.get_identity("nonexistent")
        assert result is None

    def test_should_return_none_when_generic_error(self, svc):
        with patch("overblick.identities.load_identity", side_effect=RuntimeError("boom")):
            result = svc.get_identity("broken")
        assert result is None

    def test_should_return_all_identities_when_called(self, svc):
        mock_identity = MagicMock()
        mock_identity.name = "anomal"
        mock_identity.display_name = "Anomal"
        mock_identity.description = "Test"
        mock_identity.version = "1.0.0"
        mock_identity.engagement_threshold = 0.5
        mock_identity.plugins = []
        mock_identity.capability_names = []
        mock_identity.traits = {}
        mock_identity.llm.model = "qwen3:8b"
        mock_identity.llm.temperature = 0.7
        mock_identity.llm.max_tokens = 2000
        mock_identity.quiet_hours.enabled = False
        mock_identity.quiet_hours.timezone = "UTC"
        mock_identity.quiet_hours.start_hour = 0
        mock_identity.quiet_hours.end_hour = 0
        mock_identity.schedule.heartbeat_hours = 4
        mock_identity.schedule.feed_poll_minutes = 5
        mock_identity.security.enable_preflight = True
        mock_identity.security.enable_output_safety = True
        mock_identity.identity_ref = "anomal"

        with patch("overblick.identities.list_identities", return_value=["anomal"]), \
             patch("overblick.identities.load_identity", return_value=mock_identity):
            result = svc.get_all_identities()

        assert len(result) == 1
        assert result[0]["name"] == "anomal"

    def test_should_skip_failed_identities_in_get_all(self, svc):
        with patch("overblick.identities.list_identities", return_value=["good", "bad"]), \
             patch("overblick.identities.load_identity", side_effect=FileNotFoundError("not found")):
            result = svc.get_all_identities()
        assert result == []
