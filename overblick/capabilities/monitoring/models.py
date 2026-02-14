"""
Pydantic models for host health monitoring.

These models define the data structures for system inspection results,
health inquiries between agents, and health responses.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MemoryInfo(BaseModel):
    """Memory usage information."""
    total_mb: float = 0.0
    used_mb: float = 0.0
    available_mb: float = 0.0
    percent_used: float = 0.0


class CPUInfo(BaseModel):
    """CPU information."""
    load_1m: float = 0.0
    load_5m: float = 0.0
    load_15m: float = 0.0
    core_count: int = 0


class DiskInfo(BaseModel):
    """Disk usage for a single mount point."""
    mount: str = "/"
    total_gb: float = 0.0
    used_gb: float = 0.0
    available_gb: float = 0.0
    percent_used: float = 0.0


class PowerInfo(BaseModel):
    """Power/battery information (macOS-specific)."""
    on_battery: bool = False
    battery_percent: Optional[float] = None
    time_remaining: Optional[str] = None


class HostHealth(BaseModel):
    """Complete host health snapshot."""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    hostname: str = ""
    platform: str = ""
    uptime: str = ""
    memory: MemoryInfo = MemoryInfo()
    cpu: CPUInfo = CPUInfo()
    disks: list[DiskInfo] = []
    power: PowerInfo = PowerInfo()
    errors: list[str] = []

    @property
    def health_grade(self) -> str:
        """
        Overall health grade based on resource thresholds.

        Returns:
            "good", "fair", or "poor"
        """
        issues = 0

        if self.memory.percent_used > 90:
            issues += 2
        elif self.memory.percent_used > 75:
            issues += 1

        if self.cpu.load_1m > self.cpu.core_count * 2:
            issues += 2
        elif self.cpu.load_1m > self.cpu.core_count:
            issues += 1

        for disk in self.disks:
            if disk.percent_used > 95:
                issues += 2
            elif disk.percent_used > 85:
                issues += 1

        if issues >= 3:
            return "poor"
        elif issues >= 1:
            return "fair"
        return "good"

    def to_summary(self) -> str:
        """
        Generate an LLM-consumable text summary of the host health.

        Returns:
            Multi-line summary suitable for inclusion in an LLM prompt.
        """
        lines = [
            f"Host: {self.hostname} ({self.platform})",
            f"Uptime: {self.uptime}",
            f"Health Grade: {self.health_grade.upper()}",
            "",
            f"Memory: {self.memory.used_mb:.0f}/{self.memory.total_mb:.0f} MB "
            f"({self.memory.percent_used:.1f}% used, "
            f"{self.memory.available_mb:.0f} MB available)",
            "",
            f"CPU: {self.cpu.core_count} cores, "
            f"load avg {self.cpu.load_1m:.2f} / {self.cpu.load_5m:.2f} / {self.cpu.load_15m:.2f}",
        ]

        if self.disks:
            lines.append("")
            for disk in self.disks:
                lines.append(
                    f"Disk [{disk.mount}]: {disk.used_gb:.1f}/{disk.total_gb:.1f} GB "
                    f"({disk.percent_used:.1f}% used, {disk.available_gb:.1f} GB free)"
                )

        if self.power.battery_percent is not None:
            lines.append("")
            source = "battery" if self.power.on_battery else "AC power"
            lines.append(f"Power: {source}, {self.power.battery_percent:.0f}%")
            if self.power.time_remaining:
                lines.append(f"Time remaining: {self.power.time_remaining}")

        if self.errors:
            lines.append("")
            lines.append(f"Collection errors: {', '.join(self.errors)}")

        return "\n".join(lines)


class HealthInquiry(BaseModel):
    """A health inquiry from one agent to the supervisor."""
    sender: str
    motivation: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    previous_context: Optional[str] = None


class HealthResponse(BaseModel):
    """A health response from the supervisor back to the inquiring agent."""
    responder: str
    response_text: str
    health_grade: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    health_summary: Optional[str] = None
