"""Tests for OpeningCapability — cover line 39.

Uncovered: 39 (select returns empty when not initialized).
"""

from pathlib import Path

import pytest

from overblick.capabilities.social.openings import OpeningCapability
from overblick.core.capability import CapabilityContext


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestOpeningCapabilityCoverage:
    def test_select_not_initialized(self):
        """Cover line 39: select returns empty string when not initialized."""
        ctx = make_ctx()
        cap = OpeningCapability(ctx)
        # Don't call setup
        assert cap.select() == ""

    @pytest.mark.asyncio
    async def test_setup_and_select(self):
        """Setup and select works."""
        ctx = make_ctx(config={"opening_phrases": ["Hello!", "Hi there!"]})
        cap = OpeningCapability(ctx)
        await cap.setup()

        result = cap.select()
        assert result in ["Hello!", "Hi there!"]

    @pytest.mark.asyncio
    async def test_inner_property(self):
        """inner property returns the selector."""
        ctx = make_ctx()
        cap = OpeningCapability(ctx)
        await cap.setup()
        assert cap.inner is not None
