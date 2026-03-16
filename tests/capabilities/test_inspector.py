"""
Tests for HostInspectionCapability — secure system health data collection.

Covers all uncovered lines:
- _run_command: empty args, blocked commands, timeout, FileNotFoundError, generic exceptions
- inspect(): full flow on linux/darwin, exception results for all collectors
- _collect_memory_linux: /proc/meminfo parsing, free fallback, empty output
- _collect_memory_macos: vm_stat + sysctl parsing, empty output
- _collect_cpu: OSError on getloadavg, cpu_count returns None
- _collect_uptime: various format patterns, empty output
- _collect_power: pmset parsing, battery on AC, empty output
- _parse_free_output: valid output, no Mem: line
- _parse_size_to_gb: various units including Gi, Ti, no suffix, empty, "0"
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.capabilities.monitoring.inspector import (
    HostInspectionCapability,
    _run_command,
)
from overblick.capabilities.monitoring.models import (
    CPUInfo,
    HostHealth,
    MemoryInfo,
    PowerInfo,
)


# ── _run_command tests ─────────────────────────────────────────────────────


class TestRunCommand:
    @pytest.mark.asyncio
    async def test_should_return_empty_when_no_args(self):
        result = await _run_command()
        assert result == ""

    @pytest.mark.asyncio
    async def test_should_block_non_whitelisted_command(self):
        result = await _run_command("rm", "-rf", "/")
        assert result == ""

    @pytest.mark.asyncio
    async def test_should_return_stdout_for_whitelisted_command(self):
        result = await _run_command("hostname")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_should_return_empty_on_non_zero_exit(self):
        # cat a non-existent file returns non-zero
        result = await _run_command("cat", "/nonexistent_file_test_12345")
        assert result == ""

    @pytest.mark.asyncio
    async def test_should_return_empty_on_file_not_found(self):
        # Trick: a path that looks like a whitelisted command but doesn't exist
        with patch(
            "overblick.capabilities.monitoring.inspector.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("not found"),
        ):
            result = await _run_command("hostname")
            assert result == ""

    @pytest.mark.asyncio
    async def test_should_return_empty_on_generic_exception(self):
        with patch(
            "overblick.capabilities.monitoring.inspector.asyncio.create_subprocess_exec",
            side_effect=RuntimeError("unexpected"),
        ):
            result = await _run_command("hostname")
            assert result == ""

    @pytest.mark.asyncio
    async def test_should_return_empty_on_timeout(self):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError("too slow"))
        mock_proc.kill = MagicMock()

        with patch(
            "overblick.capabilities.monitoring.inspector.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await _run_command("hostname")
            assert result == ""
            mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_should_handle_timeout_when_proc_already_exited(self):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError("too slow"))
        mock_proc.kill = MagicMock(side_effect=ProcessLookupError)

        with patch(
            "overblick.capabilities.monitoring.inspector.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await _run_command("hostname")
            assert result == ""

    @pytest.mark.asyncio
    async def test_should_extract_executable_name_from_path(self):
        """Blocks /usr/bin/rm because 'rm' is not whitelisted."""
        result = await _run_command("/usr/bin/rm", "-rf", "/tmp")
        assert result == ""


# ── HostInspectionCapability tests ─────────────────────────────────────────


class TestInspectLinux:
    @pytest.mark.asyncio
    async def test_should_collect_health_on_linux(self):
        cap = HostInspectionCapability()
        cap._platform = "linux"

        meminfo = (
            "MemTotal:       16384000 kB\n"
            "MemFree:         2048000 kB\n"
            "MemAvailable:    8192000 kB\n"
            "Buffers:          512000 kB\n"
        )

        async def mock_run(*args):
            cmd = args[0] if args else ""
            if cmd == "cat":
                return meminfo
            if cmd == "uptime":
                return " 10:30:00 up 5 days, 3:22, 2 users, load average: 1.50, 1.25, 1.10"
            return ""

        with (
            patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run),
            patch("os.getloadavg", return_value=(1.5, 1.25, 1.1)),
            patch("os.cpu_count", return_value=4),
        ):
            health = await cap.inspect()

        assert isinstance(health, HostHealth)
        assert health.platform == "linux"
        assert health.memory.total_mb > 0
        assert health.cpu.core_count == 4
        assert health.cpu.load_1m == 1.5

    @pytest.mark.asyncio
    async def test_should_handle_collector_exceptions(self):
        """All collectors raise exceptions — errors are collected."""
        cap = HostInspectionCapability()
        cap._platform = "linux"

        async def failing_memory():
            raise RuntimeError("memory fail")

        async def failing_cpu():
            raise RuntimeError("cpu fail")

        async def failing_uptime():
            raise RuntimeError("uptime fail")

        with (
            patch.object(cap, "_collect_memory", failing_memory),
            patch.object(cap, "_collect_cpu", failing_cpu),
            patch.object(cap, "_collect_uptime", failing_uptime),
        ):
            health = await cap.inspect()

        assert len(health.errors) == 3
        assert any("memory" in e for e in health.errors)
        assert any("cpu" in e for e in health.errors)
        assert any("uptime" in e for e in health.errors)


class TestInspectDarwin:
    @pytest.mark.asyncio
    async def test_should_collect_health_on_darwin_with_power(self):
        cap = HostInspectionCapability()
        cap._platform = "darwin"

        vm_stat_output = (
            "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
            "Pages free:                    50000.\n"
            "Pages inactive:                30000.\n"
            "Pages speculative:             10000.\n"
        )

        async def mock_run(*args):
            cmd = args[0] if args else ""
            if cmd == "vm_stat":
                return vm_stat_output
            if cmd == "sysctl":
                return "17179869184"  # 16 GB
            if cmd == "uptime":
                return " 10:30 up 2 days, 5:10, load averages: 2.0 1.5 1.0"
            if cmd == "pmset":
                return "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=123)\t72%; discharging; 3:45 remaining"
            return ""

        with (
            patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run),
            patch("os.getloadavg", return_value=(2.0, 1.5, 1.0)),
            patch("os.cpu_count", return_value=8),
        ):
            health = await cap.inspect()

        assert health.platform == "darwin"
        assert health.memory.total_mb > 0
        assert health.power.on_battery is True
        assert health.power.battery_percent == 72.0
        assert health.power.time_remaining == "3:45"

    @pytest.mark.asyncio
    async def test_should_handle_power_exception_on_darwin(self):
        cap = HostInspectionCapability()
        cap._platform = "darwin"

        async def failing_power():
            raise RuntimeError("power fail")

        with (
            patch.object(cap, "_collect_memory", AsyncMock(return_value=MemoryInfo())),
            patch.object(cap, "_collect_cpu", AsyncMock(return_value=CPUInfo())),
            patch.object(cap, "_collect_uptime", AsyncMock(return_value="5 days")),
            patch.object(cap, "_collect_power", failing_power),
        ):
            health = await cap.inspect()

        assert any("power" in e for e in health.errors)


# ── Memory collection tests ────────────────────────────────────────────────


class TestCollectMemoryLinux:
    @pytest.mark.asyncio
    async def test_should_parse_proc_meminfo(self):
        cap = HostInspectionCapability()
        cap._platform = "linux"

        meminfo = (
            "MemTotal:       16000000 kB\n"
            "MemFree:         2000000 kB\n"
            "MemAvailable:    8000000 kB\n"
        )

        async def mock_run(*args):
            if args[0] == "cat":
                return meminfo
            return ""

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            mem = await cap._collect_memory_linux()

        assert mem.total_mb > 0
        assert mem.available_mb > 0
        assert mem.percent_used > 0

    @pytest.mark.asyncio
    async def test_should_fallback_to_free_command(self):
        cap = HostInspectionCapability()
        cap._platform = "linux"

        free_output = (
            "              total        used        free      shared  buff/cache   available\n"
            "Mem:          16000       8000       4000         200       4000       7800\n"
            "Swap:          8000          0       8000\n"
        )

        call_count = 0

        async def mock_run(*args):
            nonlocal call_count
            call_count += 1
            if args[0] == "cat":
                return ""  # /proc/meminfo fails
            if args[0] == "free":
                return free_output
            return ""

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            mem = await cap._collect_memory_linux()

        assert mem.total_mb == 16000.0
        assert mem.used_mb == 8000.0

    @pytest.mark.asyncio
    async def test_should_return_default_when_both_fail(self):
        cap = HostInspectionCapability()
        cap._platform = "linux"

        async def mock_run(*args):
            return ""

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            mem = await cap._collect_memory_linux()

        assert mem.total_mb == 0.0


class TestCollectMemoryMacos:
    @pytest.mark.asyncio
    async def test_should_return_default_when_commands_fail(self):
        cap = HostInspectionCapability()
        cap._platform = "darwin"

        async def mock_run(*args):
            return ""

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            mem = await cap._collect_memory_macos()

        assert mem.total_mb == 0.0

    @pytest.mark.asyncio
    async def test_should_parse_vm_stat_without_page_size_match(self):
        cap = HostInspectionCapability()
        cap._platform = "darwin"

        vm_stat = (
            "Pages free:                    50000.\n"
            "Pages inactive:                30000.\n"
            "Pages speculative:             10000.\n"
        )

        async def mock_run(*args):
            if args[0] == "vm_stat":
                return vm_stat
            if args[0] == "sysctl":
                return "8589934592"  # 8 GB
            return ""

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            mem = await cap._collect_memory_macos()

        assert mem.total_mb > 0


# ── CPU collection tests ──────────────────────────────────────────────────


class TestCollectCPU:
    @pytest.mark.asyncio
    async def test_should_handle_os_error_on_getloadavg(self):
        cap = HostInspectionCapability()

        with (
            patch("os.getloadavg", side_effect=OSError("not available")),
            patch("os.cpu_count", return_value=2),
        ):
            cpu = await cap._collect_cpu()

        assert cpu.load_1m == 0.0
        assert cpu.load_5m == 0.0
        assert cpu.load_15m == 0.0
        assert cpu.core_count == 2

    @pytest.mark.asyncio
    async def test_should_handle_cpu_count_none(self):
        cap = HostInspectionCapability()

        with (
            patch("os.getloadavg", return_value=(1.0, 0.5, 0.3)),
            patch("os.cpu_count", return_value=None),
        ):
            cpu = await cap._collect_cpu()

        assert cpu.core_count == 0


# ── Uptime collection tests ───────────────────────────────────────────────


class TestCollectUptime:
    @pytest.mark.asyncio
    async def test_should_return_unknown_on_empty(self):
        cap = HostInspectionCapability()

        async def mock_run(*args):
            return ""

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            result = await cap._collect_uptime()

        assert result == "unknown"

    @pytest.mark.asyncio
    async def test_should_parse_standard_uptime_with_users(self):
        cap = HostInspectionCapability()

        async def mock_run(*args):
            return " 10:30:00 up 5 days, 3:22, 2 users, load average: 1.50, 1.25, 1.10"

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            result = await cap._collect_uptime()

        assert "5 days" in result

    @pytest.mark.asyncio
    async def test_should_parse_uptime_with_load_no_users(self):
        cap = HostInspectionCapability()

        async def mock_run(*args):
            return " 10:30 up 2 days, 5:10, load averages: 2.0 1.5 1.0"

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            result = await cap._collect_uptime()

        assert "2 days" in result

    @pytest.mark.asyncio
    async def test_should_handle_uptime_with_comma_fallback(self):
        cap = HostInspectionCapability()

        async def mock_run(*args):
            return "10:30:00 up 1 day"

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            result = await cap._collect_uptime()

        # Falls through to final fallback
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_should_handle_comma_only_output(self):
        cap = HostInspectionCapability()

        async def mock_run(*args):
            return "some,output,here"

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            result = await cap._collect_uptime()

        assert result == "some"


# ── Power collection tests ─────────────────────────────────────────────────


class TestCollectPower:
    @pytest.mark.asyncio
    async def test_should_return_default_on_empty(self):
        cap = HostInspectionCapability()

        async def mock_run(*args):
            return ""

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            power = await cap._collect_power()

        assert power.on_battery is False
        assert power.battery_percent is None

    @pytest.mark.asyncio
    async def test_should_parse_ac_power(self):
        cap = HostInspectionCapability()

        pmset_output = "Now drawing from 'AC Power'\n -InternalBattery-0\t95%; charged; 0:00 remaining"

        async def mock_run(*args):
            return pmset_output

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            power = await cap._collect_power()

        assert power.on_battery is False
        assert power.battery_percent == 95.0
        assert power.time_remaining == "0:00"

    @pytest.mark.asyncio
    async def test_should_parse_battery_without_time_remaining(self):
        cap = HostInspectionCapability()

        pmset_output = "Now drawing from 'Battery Power'\n -InternalBattery-0\t50%; discharging; (no estimate)"

        async def mock_run(*args):
            return pmset_output

        with patch("overblick.capabilities.monitoring.inspector._run_command", side_effect=mock_run):
            power = await cap._collect_power()

        assert power.on_battery is True
        assert power.battery_percent == 50.0
        assert power.time_remaining is None


# ── _parse_free_output tests ───────────────────────────────────────────────


class TestParseFreeOutput:
    def test_should_parse_valid_free_output(self):
        output = (
            "              total        used        free      shared  buff/cache   available\n"
            "Mem:          16000       8000       4000         200       4000       7800\n"
            "Swap:          8000          0       8000\n"
        )
        mem = HostInspectionCapability._parse_free_output(output)
        assert mem.total_mb == 16000.0
        assert mem.used_mb == 8000.0
        assert mem.available_mb == 7800.0

    def test_should_parse_free_output_without_available_column(self):
        output = (
            "              total        used        free\n"
            "Mem:          16000       8000       8000\n"
        )
        mem = HostInspectionCapability._parse_free_output(output)
        assert mem.total_mb == 16000.0
        assert mem.used_mb == 8000.0
        assert mem.available_mb == 8000.0  # total - used

    def test_should_return_default_when_no_mem_line(self):
        output = "Swap:          8000          0       8000\n"
        mem = HostInspectionCapability._parse_free_output(output)
        assert mem.total_mb == 0.0

    def test_should_return_default_when_mem_line_too_short(self):
        output = "Mem:          16000\n"
        mem = HostInspectionCapability._parse_free_output(output)
        assert mem.total_mb == 0.0


# ── _parse_size_to_gb tests ───────────────────────────────────────────────


class TestParseSizeToGb:
    def test_should_parse_gigabytes(self):
        assert HostInspectionCapability._parse_size_to_gb("500G") == 500.0

    def test_should_parse_terabytes(self):
        assert HostInspectionCapability._parse_size_to_gb("1T") == 1024.0

    def test_should_parse_megabytes(self):
        result = HostInspectionCapability._parse_size_to_gb("1024M")
        assert abs(result - 1.0) < 0.01

    def test_should_parse_kilobytes(self):
        result = HostInspectionCapability._parse_size_to_gb("1048576K")
        assert abs(result - 1.0) < 0.01

    def test_should_parse_bytes_suffix(self):
        result = HostInspectionCapability._parse_size_to_gb("1073741824B")
        assert abs(result - 1.0) < 0.01

    def test_should_parse_petabytes(self):
        result = HostInspectionCapability._parse_size_to_gb("1P")
        assert result == 1024 ** 2

    def test_should_parse_gi_suffix(self):
        assert HostInspectionCapability._parse_size_to_gb("500Gi") == 500.0

    def test_should_parse_mi_suffix(self):
        result = HostInspectionCapability._parse_size_to_gb("1024Mi")
        assert abs(result - 1.0) < 0.01

    def test_should_return_zero_for_empty(self):
        assert HostInspectionCapability._parse_size_to_gb("") == 0.0

    def test_should_return_zero_for_zero_string(self):
        assert HostInspectionCapability._parse_size_to_gb("0") == 0.0

    def test_should_treat_no_suffix_as_bytes(self):
        result = HostInspectionCapability._parse_size_to_gb("1073741824")
        assert abs(result - 1.0) < 0.01
