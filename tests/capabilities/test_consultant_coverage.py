"""Additional tests for PersonalityConsultantCapability — cover lines 165, 186.

Uncovered: 165 (discover_consultants: identity returns None), 186 (cached identity).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from overblick.capabilities.consulting.personality_consultant import (
    PersonalityConsultantCapability,
)
from overblick.core.capability import CapabilityContext


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


def _mock_personality(name="cherry"):
    p = MagicMock()
    p.name = name
    p.display_name = name.capitalize()
    return p


class TestConsultantCoverage:
    def test_discover_consultants_skips_none_identity(self):
        """Cover line 165: _load_identity returns None for some identities."""
        ctx = make_ctx(identity_name="stal")
        cap = PersonalityConsultantCapability(ctx)

        mock_anomal = _mock_personality("anomal")
        mock_anomal.interest_keywords = ["crypto"]

        def _load(name):
            if name == "anomal":
                return mock_anomal
            return None  # broken identity

        with (
            patch.object(cap, "_load_identity", side_effect=_load),
            patch(
                "overblick.identities.list_identities",
                return_value=["anomal", "broken"],
            ),
        ):
            result = cap.discover_consultants()

        assert "anomal" in result
        assert "broken" not in result

    def test_load_identity_uses_cache(self):
        """Cover line 186: cached personality is returned."""
        ctx = make_ctx(identity_name="test")
        cap = PersonalityConsultantCapability(ctx)

        mock_p = _mock_personality("cherry")
        cap._personality_cache["cherry"] = mock_p

        result = cap._load_identity("cherry")
        assert result is mock_p
