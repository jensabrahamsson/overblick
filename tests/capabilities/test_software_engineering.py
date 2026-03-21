"""Tests for SoftwareEngineeringCapability — STUB module."""

from unittest.mock import MagicMock

import pytest

from overblick.capabilities.engineering.software_engineering import (
    SoftwareEngineeringCapability,
)


class TestSoftwareEngineeringCapability:
    def test_should_have_correct_name(self):
        ctx = MagicMock()
        cap = SoftwareEngineeringCapability(ctx)
        assert cap.name == "software_engineering"

    def test_should_not_be_ready_by_default(self):
        ctx = MagicMock()
        cap = SoftwareEngineeringCapability(ctx)
        assert cap.configured is False

    @pytest.mark.asyncio
    async def test_setup_is_noop(self):
        ctx = MagicMock()
        cap = SoftwareEngineeringCapability(ctx)
        await cap.setup()
        assert cap.configured is False

    @pytest.mark.asyncio
    async def test_teardown_is_noop(self):
        ctx = MagicMock()
        cap = SoftwareEngineeringCapability(ctx)
        await cap.teardown()  # Should not raise
