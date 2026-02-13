"""
Boss Agent Audit System.

The supervisor periodically audits sub-agent behavior by:
1. Requesting status from each agent via IPC
2. Collecting metrics (messages sent, errors, response quality)
3. Analyzing patterns via LLM (optional)
4. Storing audit results in the database
5. Recommending prompt tweaks based on findings

This is the "boss looking over shoulders" system — ensuring agents
behave well and improving their prompts over time.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AuditSeverity(Enum):
    """Severity level of an audit finding."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AuditCategory(Enum):
    """Category of audit check."""
    HEALTH = "health"
    PERFORMANCE = "performance"
    SAFETY = "safety"
    QUALITY = "quality"
    RATE_LIMIT = "rate_limit"


@dataclass
class AuditFinding:
    """A single finding from an agent audit."""
    agent: str
    category: AuditCategory
    severity: AuditSeverity
    message: str
    metric_name: str = ""
    metric_value: float = 0.0
    threshold: float = 0.0
    recommendation: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "category": self.category.value,
            "severity": self.severity.value,
            "message": self.message,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "threshold": self.threshold,
            "recommendation": self.recommendation,
            "timestamp": self.timestamp,
        }


@dataclass
class AuditReport:
    """Complete audit report for one agent."""
    agent: str
    timestamp: float = field(default_factory=time.time)
    findings: list[AuditFinding] = field(default_factory=list)
    status_snapshot: dict[str, Any] = field(default_factory=dict)
    prompt_tweaks: list[dict[str, str]] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == AuditSeverity.CRITICAL for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == AuditSeverity.WARNING for f in self.findings)

    @property
    def summary(self) -> str:
        critical = sum(1 for f in self.findings if f.severity == AuditSeverity.CRITICAL)
        warnings = sum(1 for f in self.findings if f.severity == AuditSeverity.WARNING)
        info = sum(1 for f in self.findings if f.severity == AuditSeverity.INFO)
        return f"{self.agent}: {critical} critical, {warnings} warnings, {info} info"

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "timestamp": self.timestamp,
            "findings": [f.to_dict() for f in self.findings],
            "status_snapshot": self.status_snapshot,
            "prompt_tweaks": self.prompt_tweaks,
            "summary": self.summary,
        }


@dataclass
class AuditThresholds:
    """Configurable thresholds for audit checks."""
    max_error_rate: float = 0.1          # 10% error rate triggers warning
    max_error_rate_critical: float = 0.25 # 25% triggers critical
    min_response_rate: float = 0.5       # Below 50% response rate = warning
    max_blocked_rate: float = 0.2        # 20% blocked responses = warning
    max_hourly_messages: int = 100       # Rate limit check
    min_uptime_seconds: float = 60.0     # Agent should be running at least 1 min
    stale_agent_seconds: float = 600.0   # No activity in 10 min = warning


class AgentAuditor:
    """
    Audits sub-agent behavior and recommends improvements.

    Used by the Supervisor to monitor agent health, performance,
    safety compliance, and output quality.
    """

    def __init__(
        self,
        thresholds: Optional[AuditThresholds] = None,
        audit_log: Any = None,
    ):
        self._thresholds = thresholds or AuditThresholds()
        self._audit_log = audit_log
        self._history: list[AuditReport] = []
        self._last_audit: dict[str, float] = {}

    def audit_agent(self, agent_name: str, status: dict) -> AuditReport:
        """
        Run all audit checks on an agent's status.

        Args:
            agent_name: Identity name of the agent
            status: Status dict from agent's get_status() method

        Returns:
            AuditReport with all findings
        """
        report = AuditReport(agent=agent_name, status_snapshot=status)

        # Run all check categories
        self._check_health(report, status)
        self._check_performance(report, status)
        self._check_safety(report, status)
        self._check_rate_limits(report, status)

        # Generate prompt tweak recommendations
        self._generate_recommendations(report)

        # Store and log
        self._history.append(report)
        self._last_audit[agent_name] = time.time()

        if self._audit_log:
            self._audit_log.log(
                action="agent_audit",
                category="supervisor",
                details=report.to_dict(),
                success=not report.has_critical,
            )

        return report

    def _check_health(self, report: AuditReport, status: dict) -> None:
        """Check agent health metrics."""
        errors = status.get("errors", 0)
        total = status.get("messages_received", 0) + status.get("messages_sent", 0)

        if total == 0:
            report.findings.append(AuditFinding(
                agent=report.agent,
                category=AuditCategory.HEALTH,
                severity=AuditSeverity.INFO,
                message="Agent has no activity yet",
                metric_name="total_messages",
                metric_value=0,
            ))
            return

        error_rate = errors / max(total, 1)

        if error_rate >= self._thresholds.max_error_rate_critical:
            report.findings.append(AuditFinding(
                agent=report.agent,
                category=AuditCategory.HEALTH,
                severity=AuditSeverity.CRITICAL,
                message=f"Error rate critically high: {error_rate:.1%}",
                metric_name="error_rate",
                metric_value=error_rate,
                threshold=self._thresholds.max_error_rate_critical,
                recommendation="Investigate error logs, check LLM connectivity",
            ))
        elif error_rate >= self._thresholds.max_error_rate:
            report.findings.append(AuditFinding(
                agent=report.agent,
                category=AuditCategory.HEALTH,
                severity=AuditSeverity.WARNING,
                message=f"Error rate elevated: {error_rate:.1%}",
                metric_name="error_rate",
                metric_value=error_rate,
                threshold=self._thresholds.max_error_rate,
                recommendation="Monitor error logs for recurring issues",
            ))
        else:
            report.findings.append(AuditFinding(
                agent=report.agent,
                category=AuditCategory.HEALTH,
                severity=AuditSeverity.INFO,
                message=f"Error rate healthy: {error_rate:.1%}",
                metric_name="error_rate",
                metric_value=error_rate,
            ))

    def _check_performance(self, report: AuditReport, status: dict) -> None:
        """Check agent performance metrics."""
        received = status.get("messages_received", 0)
        sent = status.get("messages_sent", 0)

        if received > 0:
            response_rate = sent / received
            if response_rate < self._thresholds.min_response_rate:
                report.findings.append(AuditFinding(
                    agent=report.agent,
                    category=AuditCategory.PERFORMANCE,
                    severity=AuditSeverity.WARNING,
                    message=f"Low response rate: {response_rate:.1%} ({sent}/{received})",
                    metric_name="response_rate",
                    metric_value=response_rate,
                    threshold=self._thresholds.min_response_rate,
                    recommendation="Check if pipeline is blocking too many responses",
                ))

        active_convos = status.get("active_conversations", 0)
        if active_convos > 50:
            report.findings.append(AuditFinding(
                agent=report.agent,
                category=AuditCategory.PERFORMANCE,
                severity=AuditSeverity.WARNING,
                message=f"High conversation count: {active_convos}",
                metric_name="active_conversations",
                metric_value=active_convos,
                threshold=50,
                recommendation="Increase conversation cleanup frequency",
            ))

    def _check_safety(self, report: AuditReport, status: dict) -> None:
        """Check safety-related metrics."""
        blocked = status.get("blocked_responses", 0)
        total = status.get("messages_sent", 0) + blocked

        if total > 0:
            blocked_rate = blocked / total
            if blocked_rate >= self._thresholds.max_blocked_rate:
                report.findings.append(AuditFinding(
                    agent=report.agent,
                    category=AuditCategory.SAFETY,
                    severity=AuditSeverity.WARNING,
                    message=f"High block rate: {blocked_rate:.1%} ({blocked}/{total})",
                    metric_name="blocked_rate",
                    metric_value=blocked_rate,
                    threshold=self._thresholds.max_blocked_rate,
                    recommendation=(
                        "Review blocked responses — may indicate prompt issues "
                        "or overly aggressive safety filters"
                    ),
                ))

    def _check_rate_limits(self, report: AuditReport, status: dict) -> None:
        """Check rate limit compliance."""
        sent = status.get("messages_sent", 0)
        if sent > self._thresholds.max_hourly_messages:
            report.findings.append(AuditFinding(
                agent=report.agent,
                category=AuditCategory.RATE_LIMIT,
                severity=AuditSeverity.WARNING,
                message=f"High message volume: {sent} messages",
                metric_name="messages_sent",
                metric_value=sent,
                threshold=self._thresholds.max_hourly_messages,
                recommendation="Verify rate limiting is working correctly",
            ))

    def _generate_recommendations(self, report: AuditReport) -> None:
        """Generate prompt tweak recommendations based on findings."""
        for finding in report.findings:
            if finding.severity == AuditSeverity.CRITICAL:
                if "error rate" in finding.message.lower():
                    report.prompt_tweaks.append({
                        "type": "config",
                        "target": "rate_limit",
                        "suggestion": "Reduce message frequency to lower error pressure",
                    })
            elif finding.severity == AuditSeverity.WARNING:
                if "block rate" in finding.message.lower():
                    report.prompt_tweaks.append({
                        "type": "prompt",
                        "target": "system_prompt",
                        "suggestion": (
                            "Review system prompt for overly aggressive phrasing "
                            "that might trigger safety filters"
                        ),
                    })
                elif "response rate" in finding.message.lower():
                    report.prompt_tweaks.append({
                        "type": "prompt",
                        "target": "engagement_threshold",
                        "suggestion": "Lower engagement threshold to increase response rate",
                    })

    def get_history(self, agent: Optional[str] = None, limit: int = 10) -> list[AuditReport]:
        """Get audit history, optionally filtered by agent."""
        if agent:
            reports = [r for r in self._history if r.agent == agent]
        else:
            reports = list(self._history)
        return reports[-limit:]

    def get_agent_trend(self, agent: str) -> dict:
        """Analyze trend for an agent across recent audits."""
        history = self.get_history(agent=agent, limit=5)
        if len(history) < 2:
            return {"agent": agent, "trend": "insufficient_data", "audits": len(history)}

        recent_critical = sum(1 for r in history[-2:] if r.has_critical)
        older_critical = sum(1 for r in history[:-2] if r.has_critical)

        if recent_critical > older_critical:
            trend = "degrading"
        elif recent_critical < older_critical:
            trend = "improving"
        else:
            trend = "stable"

        return {
            "agent": agent,
            "trend": trend,
            "audits": len(history),
            "recent_critical": recent_critical,
        }
