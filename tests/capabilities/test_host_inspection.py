"""
Tests for the monitoring capability: models, inspector, and command whitelisting.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from overblick.capabilities.monitoring.models import (
    CPUInfo,
    DiskInfo,
    HealthInquiry,
    HealthResponse,
    HostHealth,
    MemoryInfo,
    PowerInfo,
)
from overblick.capabilities.monitoring.inspector import (
    HostInspectionCapability,
    _ALLOWED_COMMANDS,
    _run_command,
)


# ---------------------------------------------------------------------------
# Model Tests
# ---------------------------------------------------------------------------

class TestHostHealthModel:
    """Test HostHealth model properties and serialization."""

    def test_health_grade_good(self):
        """Good grade when all metrics are within thresholds."""
        health = HostHealth(
            memory=MemoryInfo(total_mb=16000, used_mb=8000, available_mb=8000, percent_used=50),
            cpu=CPUInfo(load_1m=1.0, load_5m=1.0, load_15m=1.0, core_count=8),
            disks=[DiskInfo(mount="/", total_gb=500, used_gb=200, available_gb=300, percent_used=40)],
        )
        assert health.health_grade == "good"

    def test_health_grade_fair_high_memory(self):
        """Fair grade when memory usage is elevated."""
        health = HostHealth(
            memory=MemoryInfo(total_mb=16000, used_mb=13000, available_mb=3000, percent_used=81),
            cpu=CPUInfo(load_1m=1.0, core_count=8),
        )
        assert health.health_grade == "fair"

    def test_health_grade_poor_multiple_issues(self):
        """Poor grade when multiple metrics are critical."""
        health = HostHealth(
            memory=MemoryInfo(total_mb=16000, used_mb=15000, available_mb=1000, percent_used=93),
            cpu=CPUInfo(load_1m=20.0, core_count=8),
            disks=[DiskInfo(mount="/", total_gb=500, used_gb=490, available_gb=10, percent_used=98)],
        )
        assert health.health_grade == "poor"

    def test_health_grade_fair_high_cpu(self):
        """Fair grade when CPU load exceeds core count."""
        health = HostHealth(
            memory=MemoryInfo(total_mb=16000, used_mb=8000, percent_used=50),
            cpu=CPUInfo(load_1m=10.0, core_count=8),
        )
        assert health.health_grade == "fair"

    def test_to_summary_contains_key_info(self):
        """Summary text includes all critical information."""
        health = HostHealth(
            hostname="testhost",
            platform="darwin",
            uptime="5 days",
            memory=MemoryInfo(total_mb=16000, used_mb=8000, available_mb=8000, percent_used=50),
            cpu=CPUInfo(load_1m=1.5, load_5m=1.2, load_15m=1.0, core_count=8),
            disks=[DiskInfo(mount="/", total_gb=500, used_gb=200, available_gb=300, percent_used=40)],
        )
        summary = health.to_summary()

        assert "testhost" in summary
        assert "darwin" in summary
        assert "5 days" in summary
        assert "GOOD" in summary
        assert "8000/16000 MB" in summary
        assert "8 cores" in summary
        assert "200.0/500.0 GB" in summary

    def test_to_summary_includes_power(self):
        """Summary includes power info when battery data is available."""
        health = HostHealth(
            hostname="macbook",
            platform="darwin",
            power=PowerInfo(on_battery=True, battery_percent=45.0, time_remaining="2:30"),
        )
        summary = health.to_summary()
        assert "battery" in summary
        assert "45%" in summary
        assert "2:30" in summary

    def test_to_summary_includes_errors(self):
        """Summary includes collection errors."""
        health = HostHealth(errors=["memory: timeout", "disk: not found"])
        summary = health.to_summary()
        assert "Collection errors" in summary
        assert "memory: timeout" in summary


class TestHealthModels:
    """Test HealthInquiry and HealthResponse models."""

    def test_health_inquiry_creation(self):
        inquiry = HealthInquiry(
            sender="natt",
            motivation="The substrate that holds us — does it ache?",
        )
        assert inquiry.sender == "natt"
        assert "substrate" in inquiry.motivation
        assert inquiry.timestamp  # Auto-generated

    def test_health_response_creation(self):
        response = HealthResponse(
            responder="anomal",
            response_text="The host is doing rather well.",
            health_grade="good",
        )
        assert response.responder == "anomal"
        assert response.health_grade == "good"


# ---------------------------------------------------------------------------
# Command Whitelisting Tests
# ---------------------------------------------------------------------------

class TestCommandWhitelisting:
    """Test that only whitelisted commands can execute."""

    def test_allowed_commands_is_frozenset(self):
        """Whitelist is immutable (frozenset)."""
        assert isinstance(_ALLOWED_COMMANDS, frozenset)

    def test_expected_commands_in_whitelist(self):
        """All expected commands are whitelisted."""
        expected = {"vm_stat", "sysctl", "df", "uptime", "pmset", "free", "cat", "hostname"}
        assert expected.issubset(_ALLOWED_COMMANDS)

    def test_dangerous_commands_not_in_whitelist(self):
        """Dangerous commands are NOT whitelisted."""
        dangerous = {"rm", "bash", "sh", "curl", "wget", "python", "python3", "sudo", "chmod"}
        assert not dangerous.intersection(_ALLOWED_COMMANDS)

    @pytest.mark.asyncio
    async def test_blocked_command_returns_empty(self):
        """Non-whitelisted commands return empty string."""
        result = await _run_command("rm", "-rf", "/")
        assert result == ""

    @pytest.mark.asyncio
    async def test_empty_command_returns_empty(self):
        """Empty command returns empty string."""
        result = await _run_command()
        assert result == ""


# ---------------------------------------------------------------------------
# Inspector Tests
# ---------------------------------------------------------------------------

class TestHostInspectionCapability:
    """Test the inspector with mocked subprocess commands."""

    @pytest.mark.asyncio
    async def test_inspect_returns_host_health(self):
        """inspect() returns a HostHealth object with data."""
        inspector = HostInspectionCapability()

        with patch("overblick.capabilities.monitoring.inspector._run_command") as mock_cmd:
            # Mock all commands
            async def _mock_run(*args):
                cmd = args[0] if args else ""
                if cmd == "vm_stat":
                    return (
                        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                        "Pages free:                               100000.\n"
                        "Pages active:                             200000.\n"
                        "Pages inactive:                           50000.\n"
                        "Pages speculative:                        10000.\n"
                    )
                if cmd == "sysctl":
                    return "17179869184"  # 16 GB
                if cmd == "df":
                    return (
                        "Filesystem     Size   Used  Avail Capacity  Mounted on\n"
                        "/dev/disk1s1  466G   200G   266G    43%    /\n"
                    )
                if cmd == "uptime":
                    return " 14:30  up 5 days, 3:45, 2 users, load averages: 1.50 1.30 1.20"
                if cmd == "pmset":
                    return "Now drawing from 'AC Power'\n -InternalBattery-0 (id=123)\t85%; charged; 0:00 remaining"
                return ""

            mock_cmd.side_effect = _mock_run

            health = await inspector.inspect()

            assert isinstance(health, HostHealth)
            assert health.hostname  # Should be set from socket.gethostname()
            assert health.uptime
            assert health.cpu.core_count > 0  # Uses os.cpu_count()

    @pytest.mark.asyncio
    async def test_inspect_handles_partial_failures(self):
        """Inspector returns partial data when some collectors fail."""
        inspector = HostInspectionCapability()

        with patch("overblick.capabilities.monitoring.inspector._run_command") as mock_cmd:
            # All commands fail
            mock_cmd.return_value = ""

            health = await inspector.inspect()

            assert isinstance(health, HostHealth)
            # CPU info should still work (uses os.getloadavg, not subprocess)
            assert health.cpu.core_count > 0

    @pytest.mark.asyncio
    async def test_parse_size_to_gb(self):
        """Size string parsing handles various formats."""
        inspector = HostInspectionCapability()

        assert inspector._parse_size_to_gb("500G") == 500.0
        assert inspector._parse_size_to_gb("1.5T") == 1536.0
        assert abs(inspector._parse_size_to_gb("256M") - 0.25) < 0.01
        assert inspector._parse_size_to_gb("0") == 0.0

    @pytest.mark.asyncio
    async def test_parse_free_output(self):
        """Linux free -m output parsing."""
        inspector = HostInspectionCapability()
        output = (
            "              total        used        free      shared  buff/cache   available\n"
            "Mem:          16384        8000        4000         200        4384        8000\n"
        )
        mem = inspector._parse_free_output(output)
        assert mem.total_mb == 16384.0
        assert mem.used_mb == 8000.0
