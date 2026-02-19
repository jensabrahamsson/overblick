"""
Host inspection capability — secure system health data collection.

Security:
- Whitelisted commands ONLY (frozenset, immutable)
- asyncio.create_subprocess_exec (no shell=True, no user input in args)
- 5-second timeout per command
- Platform detection for macOS vs Linux
- Each collector isolated — partial failures return partial data
"""

import asyncio
import logging
import os
import re
import socket
import sys
from pathlib import Path

from overblick.capabilities.monitoring.models import (
    CPUInfo,
    HostHealth,
    MemoryInfo,
    PowerInfo,
)

logger = logging.getLogger(__name__)

# Whitelisted executables — ONLY these can be run
_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "vm_stat",
    "sysctl",
    "ps",
    "uptime",
    "pmset",
    "free",
    "nproc",
    "cat",
    "hostname",
})

# Timeout per command (seconds)
_CMD_TIMEOUT = 5.0


async def _run_command(*args: str) -> str:
    """
    Run a whitelisted command and return its stdout.

    Security:
    - Command must be in _ALLOWED_COMMANDS
    - Uses create_subprocess_exec (no shell interpretation)
    - 5-second timeout prevents hangs

    Returns:
        stdout as string, or empty string on failure
    """
    if not args:
        return ""

    executable = Path(args[0]).name
    if executable not in _ALLOWED_COMMANDS:
        logger.warning("Blocked non-whitelisted command: %s", executable)
        return ""

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_CMD_TIMEOUT
        )
        if proc.returncode != 0:
            logger.debug("Command %s returned %d: %s", args, proc.returncode, stderr.decode().strip())
            return ""
        return stdout.decode().strip()
    except asyncio.TimeoutError:
        logger.warning("Command timed out after %.1fs: %s", _CMD_TIMEOUT, args)
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return ""
    except FileNotFoundError:
        logger.debug("Command not found: %s", args[0])
        return ""
    except Exception as e:
        logger.warning("Command execution failed: %s — %s", args, e)
        return ""


class HostInspectionCapability:
    """
    Inspect host system health using only whitelisted commands.

    Platform-aware: detects macOS vs Linux and uses appropriate commands.
    Each collector is isolated — if one fails, others still return data.
    """

    def __init__(self):
        self._platform = sys.platform

    async def inspect(self) -> HostHealth:
        """
        Collect a full health snapshot of the host system.

        Returns:
            HostHealth with all available data. Fields that failed
            to collect will have default values; errors are listed
            in HostHealth.errors.
        """
        errors: list[str] = []

        # Run all collectors concurrently
        memory_task = self._collect_memory()
        cpu_task = self._collect_cpu()
        uptime_task = self._collect_uptime()

        tasks = [memory_task, cpu_task, uptime_task]

        # Power info only on macOS
        power_task = None
        if self._platform == "darwin":
            power_task = self._collect_power()
            tasks.append(power_task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Unpack results
        memory = results[0] if not isinstance(results[0], Exception) else MemoryInfo()
        if isinstance(results[0], Exception):
            errors.append(f"memory: {results[0]}")

        cpu = results[1] if not isinstance(results[1], Exception) else CPUInfo()
        if isinstance(results[1], Exception):
            errors.append(f"cpu: {results[1]}")

        uptime = results[2] if not isinstance(results[2], Exception) else ""
        if isinstance(results[2], Exception):
            errors.append(f"uptime: {results[2]}")

        power = PowerInfo()
        if power_task is not None:
            power = results[3] if not isinstance(results[3], Exception) else PowerInfo()
            if isinstance(results[3], Exception):
                errors.append(f"power: {results[3]}")

        return HostHealth(
            hostname=socket.gethostname(),
            platform=self._platform,
            uptime=uptime,
            memory=memory,
            cpu=cpu,
            power=power,
            errors=errors,
        )

    async def _collect_memory(self) -> MemoryInfo:
        """Collect memory usage (macOS: vm_stat + sysctl, Linux: free)."""
        if self._platform == "darwin":
            return await self._collect_memory_macos()
        return await self._collect_memory_linux()

    async def _collect_memory_macos(self) -> MemoryInfo:
        """Collect memory from macOS vm_stat + sysctl."""
        vm_out = await _run_command("vm_stat")
        mem_out = await _run_command("sysctl", "-n", "hw.memsize")

        if not vm_out or not mem_out:
            return MemoryInfo()

        total_bytes = int(mem_out.strip())
        total_mb = total_bytes / (1024 * 1024)

        # Parse vm_stat output
        page_size = 4096  # default macOS page size
        ps_match = re.search(r"page size of (\d+) bytes", vm_out)
        if ps_match:
            page_size = int(ps_match.group(1))

        def _pages(label: str) -> int:
            match = re.search(rf"{label}:\s+(\d+)", vm_out)
            return int(match.group(1)) if match else 0

        free_pages = _pages("Pages free")
        inactive_pages = _pages("Pages inactive")
        speculative_pages = _pages("Pages speculative")

        available_mb = (free_pages + inactive_pages + speculative_pages) * page_size / (1024 * 1024)
        used_mb = total_mb - available_mb

        return MemoryInfo(
            total_mb=round(total_mb, 1),
            used_mb=round(max(0, used_mb), 1),
            available_mb=round(available_mb, 1),
            percent_used=round(used_mb / total_mb * 100, 1) if total_mb > 0 else 0,
        )

    async def _collect_memory_linux(self) -> MemoryInfo:
        """Collect memory from Linux /proc/meminfo via cat."""
        output = await _run_command("cat", "/proc/meminfo")
        if not output:
            # Fallback: try free command
            output = await _run_command("free", "-m")
            if not output:
                return MemoryInfo()
            return self._parse_free_output(output)

        info = {}
        for line in output.splitlines():
            parts = line.split(":")
            if len(parts) == 2:
                key = parts[0].strip()
                val = parts[1].strip().split()[0]
                info[key] = int(val)

        total_kb = info.get("MemTotal", 0)
        available_kb = info.get("MemAvailable", 0)
        total_mb = total_kb / 1024
        available_mb = available_kb / 1024
        used_mb = total_mb - available_mb

        return MemoryInfo(
            total_mb=round(total_mb, 1),
            used_mb=round(max(0, used_mb), 1),
            available_mb=round(available_mb, 1),
            percent_used=round(used_mb / total_mb * 100, 1) if total_mb > 0 else 0,
        )

    @staticmethod
    def _parse_free_output(output: str) -> MemoryInfo:
        """Parse output of 'free -m' command."""
        for line in output.splitlines():
            if line.startswith("Mem:"):
                parts = line.split()
                if len(parts) >= 4:
                    total = float(parts[1])
                    used = float(parts[2])
                    available = float(parts[6]) if len(parts) >= 7 else total - used
                    return MemoryInfo(
                        total_mb=total,
                        used_mb=used,
                        available_mb=available,
                        percent_used=round(used / total * 100, 1) if total > 0 else 0,
                    )
        return MemoryInfo()

    async def _collect_cpu(self) -> CPUInfo:
        """Collect CPU info (load averages + core count)."""
        # Load averages from os.getloadavg() — no subprocess needed
        try:
            load = os.getloadavg()
            load_1m, load_5m, load_15m = load
        except OSError:
            load_1m = load_5m = load_15m = 0.0

        # Core count
        core_count = os.cpu_count() or 0

        return CPUInfo(
            load_1m=round(load_1m, 2),
            load_5m=round(load_5m, 2),
            load_15m=round(load_15m, 2),
            core_count=core_count,
        )

    @staticmethod
    def _parse_size_to_gb(size_str: str) -> float:
        """Parse df -h size string (e.g. '500G', '1.2T', '256M') to GB."""
        size_str = size_str.strip()
        if not size_str or size_str == "0":
            return 0.0

        multipliers = {"B": 1 / (1024**3), "K": 1 / (1024**2), "M": 1 / 1024, "G": 1, "T": 1024, "P": 1024**2}

        suffix = size_str[-1].upper()
        if suffix == "I":
            # Handle "Gi", "Mi" etc
            suffix = size_str[-2].upper()
            number = size_str[:-2]
        elif suffix in multipliers:
            number = size_str[:-1]
        else:
            # No suffix, assume bytes
            return float(size_str) / (1024**3)

        return float(number) * multipliers.get(suffix, 1)

    async def _collect_uptime(self) -> str:
        """Collect system uptime."""
        output = await _run_command("uptime")
        if not output:
            return "unknown"

        # Extract the uptime portion (before "load")
        match = re.search(r"up\s+(.+?),\s+\d+\s+users?", output)
        if match:
            return match.group(1).strip()

        # Fallback: extract everything between "up" and first load avg
        match = re.search(r"up\s+(.+?)(?:,\s+load|\s+load)", output)
        if match:
            return match.group(1).strip().rstrip(",")

        return output.split(",")[0] if "," in output else output

    async def _collect_power(self) -> PowerInfo:
        """Collect power/battery info (macOS only via pmset)."""
        output = await _run_command("pmset", "-g", "batt")
        if not output:
            return PowerInfo()

        on_battery = "Battery Power" in output

        # Parse battery percentage
        percent_match = re.search(r"(\d+)%", output)
        battery_percent = float(percent_match.group(1)) if percent_match else None

        # Parse time remaining
        time_match = re.search(r"(\d+:\d+) remaining", output)
        time_remaining = time_match.group(1) if time_match else None

        return PowerInfo(
            on_battery=on_battery,
            battery_percent=battery_percent,
            time_remaining=time_remaining,
        )
