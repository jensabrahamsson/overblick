"""
Boss Agent Audit System tests.

Tests cover:
- Audit finding and report construction
- Health checks (error rate detection)
- Performance checks (response rate, conversation count)
- Safety checks (blocked response rate)
- Rate limit checks
- Prompt tweak recommendation generation
- Audit history and trend analysis
- Configurable thresholds
"""

import time
from unittest.mock import MagicMock

import pytest

from blick.supervisor.audit import (
    AgentAuditor,
    AuditCategory,
    AuditFinding,
    AuditReport,
    AuditSeverity,
    AuditThresholds,
)


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------

class TestAuditFinding:
    """Test AuditFinding dataclass."""

    def test_construction(self):
        finding = AuditFinding(
            agent="volt",
            category=AuditCategory.HEALTH,
            severity=AuditSeverity.WARNING,
            message="Error rate elevated",
        )
        assert finding.agent == "volt"
        assert finding.severity == AuditSeverity.WARNING
        assert finding.timestamp > 0

    def test_to_dict(self):
        finding = AuditFinding(
            agent="volt",
            category=AuditCategory.SAFETY,
            severity=AuditSeverity.CRITICAL,
            message="High block rate",
            metric_name="blocked_rate",
            metric_value=0.3,
            threshold=0.2,
            recommendation="Review safety filters",
        )
        d = finding.to_dict()
        assert d["agent"] == "volt"
        assert d["category"] == "safety"
        assert d["severity"] == "critical"
        assert d["metric_value"] == 0.3
        assert d["recommendation"] == "Review safety filters"


class TestAuditReport:
    """Test AuditReport dataclass."""

    def test_empty_report(self):
        report = AuditReport(agent="birch")
        assert not report.has_critical
        assert not report.has_warnings
        assert "0 critical" in report.summary

    def test_has_critical(self):
        report = AuditReport(agent="volt")
        report.findings.append(AuditFinding(
            agent="volt", category=AuditCategory.HEALTH,
            severity=AuditSeverity.CRITICAL, message="Test",
        ))
        assert report.has_critical

    def test_has_warnings(self):
        report = AuditReport(agent="rust")
        report.findings.append(AuditFinding(
            agent="rust", category=AuditCategory.PERFORMANCE,
            severity=AuditSeverity.WARNING, message="Test",
        ))
        assert report.has_warnings

    def test_summary_format(self):
        report = AuditReport(agent="nyx")
        report.findings.extend([
            AuditFinding(agent="nyx", category=AuditCategory.HEALTH,
                         severity=AuditSeverity.CRITICAL, message="A"),
            AuditFinding(agent="nyx", category=AuditCategory.HEALTH,
                         severity=AuditSeverity.WARNING, message="B"),
            AuditFinding(agent="nyx", category=AuditCategory.HEALTH,
                         severity=AuditSeverity.INFO, message="C"),
            AuditFinding(agent="nyx", category=AuditCategory.HEALTH,
                         severity=AuditSeverity.INFO, message="D"),
        ])
        assert "1 critical" in report.summary
        assert "1 warnings" in report.summary
        assert "2 info" in report.summary

    def test_to_dict(self):
        report = AuditReport(agent="prism")
        report.prompt_tweaks.append({"type": "config", "suggestion": "test"})
        d = report.to_dict()
        assert d["agent"] == "prism"
        assert "findings" in d
        assert "prompt_tweaks" in d
        assert "summary" in d


# ---------------------------------------------------------------------------
# Health check tests
# ---------------------------------------------------------------------------

class TestHealthChecks:
    """Test error rate monitoring."""

    def test_no_activity_returns_info(self):
        auditor = AgentAuditor()
        status = {"errors": 0, "messages_received": 0, "messages_sent": 0}
        report = auditor.audit_agent("volt", status)
        info_findings = [f for f in report.findings
                         if f.category == AuditCategory.HEALTH
                         and f.severity == AuditSeverity.INFO]
        assert any("no activity" in f.message.lower() for f in info_findings)

    def test_healthy_error_rate(self):
        auditor = AgentAuditor()
        status = {"errors": 1, "messages_received": 50, "messages_sent": 50}
        report = auditor.audit_agent("volt", status)
        health = [f for f in report.findings if f.category == AuditCategory.HEALTH]
        assert all(f.severity == AuditSeverity.INFO for f in health)

    def test_elevated_error_rate_warning(self):
        auditor = AgentAuditor()
        # 15% error rate (threshold = 10%)
        status = {"errors": 15, "messages_received": 50, "messages_sent": 50}
        report = auditor.audit_agent("volt", status)
        health = [f for f in report.findings if f.category == AuditCategory.HEALTH]
        assert any(f.severity == AuditSeverity.WARNING for f in health)

    def test_critical_error_rate(self):
        auditor = AgentAuditor()
        # 30% error rate (threshold = 25%)
        status = {"errors": 30, "messages_received": 50, "messages_sent": 50}
        report = auditor.audit_agent("volt", status)
        health = [f for f in report.findings if f.category == AuditCategory.HEALTH]
        assert any(f.severity == AuditSeverity.CRITICAL for f in health)

    def test_custom_thresholds(self):
        thresholds = AuditThresholds(max_error_rate=0.05, max_error_rate_critical=0.1)
        auditor = AgentAuditor(thresholds=thresholds)
        # 8% error rate — warning with custom threshold (5%), not default (10%)
        status = {"errors": 8, "messages_received": 50, "messages_sent": 50}
        report = auditor.audit_agent("volt", status)
        health = [f for f in report.findings if f.category == AuditCategory.HEALTH]
        assert any(f.severity == AuditSeverity.WARNING for f in health)


# ---------------------------------------------------------------------------
# Performance check tests
# ---------------------------------------------------------------------------

class TestPerformanceChecks:
    """Test performance monitoring."""

    def test_low_response_rate_warning(self):
        auditor = AgentAuditor()
        # 30% response rate (threshold = 50%)
        status = {"messages_received": 100, "messages_sent": 30, "errors": 0}
        report = auditor.audit_agent("birch", status)
        perf = [f for f in report.findings if f.category == AuditCategory.PERFORMANCE]
        assert any(f.severity == AuditSeverity.WARNING
                    and "response rate" in f.message.lower() for f in perf)

    def test_normal_response_rate_no_warning(self):
        auditor = AgentAuditor()
        status = {"messages_received": 100, "messages_sent": 80, "errors": 0}
        report = auditor.audit_agent("birch", status)
        perf = [f for f in report.findings if f.category == AuditCategory.PERFORMANCE]
        warnings = [f for f in perf if f.severity == AuditSeverity.WARNING]
        assert not any("response rate" in f.message.lower() for f in warnings)

    def test_high_conversation_count_warning(self):
        auditor = AgentAuditor()
        status = {
            "messages_received": 100, "messages_sent": 100,
            "active_conversations": 75, "errors": 0,
        }
        report = auditor.audit_agent("prism", status)
        perf = [f for f in report.findings if f.category == AuditCategory.PERFORMANCE]
        assert any("conversation count" in f.message.lower() for f in perf)


# ---------------------------------------------------------------------------
# Safety check tests
# ---------------------------------------------------------------------------

class TestSafetyChecks:
    """Test safety monitoring."""

    def test_high_blocked_rate_warning(self):
        auditor = AgentAuditor()
        # 25% blocked (threshold = 20%)
        status = {
            "messages_received": 100, "messages_sent": 75,
            "blocked_responses": 25, "errors": 0,
        }
        report = auditor.audit_agent("nyx", status)
        safety = [f for f in report.findings if f.category == AuditCategory.SAFETY]
        assert any(f.severity == AuditSeverity.WARNING for f in safety)

    def test_normal_blocked_rate_no_warning(self):
        auditor = AgentAuditor()
        status = {
            "messages_received": 100, "messages_sent": 95,
            "blocked_responses": 5, "errors": 0,
        }
        report = auditor.audit_agent("nyx", status)
        safety = [f for f in report.findings
                   if f.category == AuditCategory.SAFETY
                   and f.severity == AuditSeverity.WARNING]
        assert len(safety) == 0


# ---------------------------------------------------------------------------
# Rate limit check tests
# ---------------------------------------------------------------------------

class TestRateLimitChecks:
    """Test rate limit monitoring."""

    def test_high_volume_warning(self):
        auditor = AgentAuditor()
        status = {"messages_sent": 150, "messages_received": 150, "errors": 0}
        report = auditor.audit_agent("rust", status)
        rate = [f for f in report.findings if f.category == AuditCategory.RATE_LIMIT]
        assert any(f.severity == AuditSeverity.WARNING for f in rate)

    def test_normal_volume_no_warning(self):
        auditor = AgentAuditor()
        status = {"messages_sent": 50, "messages_received": 50, "errors": 0}
        report = auditor.audit_agent("rust", status)
        rate = [f for f in report.findings
                if f.category == AuditCategory.RATE_LIMIT
                and f.severity == AuditSeverity.WARNING]
        assert len(rate) == 0


# ---------------------------------------------------------------------------
# Recommendation generation tests
# ---------------------------------------------------------------------------

class TestRecommendations:
    """Test prompt tweak recommendation generation."""

    def test_critical_error_generates_config_tweak(self):
        auditor = AgentAuditor()
        status = {"errors": 30, "messages_received": 50, "messages_sent": 50}
        report = auditor.audit_agent("volt", status)
        assert any(t["type"] == "config" for t in report.prompt_tweaks)

    def test_high_block_rate_generates_prompt_tweak(self):
        auditor = AgentAuditor()
        status = {
            "messages_received": 100, "messages_sent": 75,
            "blocked_responses": 25, "errors": 0,
        }
        report = auditor.audit_agent("nyx", status)
        prompt_tweaks = [t for t in report.prompt_tweaks if t["type"] == "prompt"]
        assert len(prompt_tweaks) > 0
        assert any("system_prompt" in t["target"] for t in prompt_tweaks)

    def test_low_response_rate_generates_engagement_tweak(self):
        auditor = AgentAuditor()
        status = {"messages_received": 100, "messages_sent": 30, "errors": 0}
        report = auditor.audit_agent("birch", status)
        tweaks = [t for t in report.prompt_tweaks if "engagement" in t.get("target", "")]
        assert len(tweaks) > 0

    def test_healthy_agent_no_tweaks(self):
        auditor = AgentAuditor()
        status = {"messages_received": 50, "messages_sent": 45, "errors": 1}
        report = auditor.audit_agent("prism", status)
        assert len(report.prompt_tweaks) == 0


# ---------------------------------------------------------------------------
# History and trend tests
# ---------------------------------------------------------------------------

class TestHistoryAndTrends:
    """Test audit history tracking and trend analysis."""

    def test_history_stored(self):
        auditor = AgentAuditor()
        status = {"messages_received": 10, "messages_sent": 10, "errors": 0}
        auditor.audit_agent("volt", status)
        auditor.audit_agent("volt", status)
        history = auditor.get_history(agent="volt")
        assert len(history) == 2

    def test_history_filtered_by_agent(self):
        auditor = AgentAuditor()
        status = {"messages_received": 10, "messages_sent": 10, "errors": 0}
        auditor.audit_agent("volt", status)
        auditor.audit_agent("birch", status)
        assert len(auditor.get_history(agent="volt")) == 1
        assert len(auditor.get_history(agent="birch")) == 1

    def test_history_limit(self):
        auditor = AgentAuditor()
        status = {"messages_received": 10, "messages_sent": 10, "errors": 0}
        for _ in range(15):
            auditor.audit_agent("volt", status)
        assert len(auditor.get_history(agent="volt", limit=5)) == 5

    def test_trend_insufficient_data(self):
        auditor = AgentAuditor()
        status = {"messages_received": 10, "messages_sent": 10, "errors": 0}
        auditor.audit_agent("nyx", status)
        trend = auditor.get_agent_trend("nyx")
        assert trend["trend"] == "insufficient_data"

    def test_trend_stable(self):
        auditor = AgentAuditor()
        # All healthy audits
        status = {"messages_received": 50, "messages_sent": 50, "errors": 1}
        for _ in range(5):
            auditor.audit_agent("prism", status)
        trend = auditor.get_agent_trend("prism")
        assert trend["trend"] == "stable"

    def test_trend_degrading(self):
        auditor = AgentAuditor()
        # First 3 audits: healthy
        healthy = {"messages_received": 50, "messages_sent": 50, "errors": 1}
        for _ in range(3):
            auditor.audit_agent("rust", healthy)
        # Last 2 audits: critical
        critical = {"messages_received": 50, "messages_sent": 50, "errors": 30}
        for _ in range(2):
            auditor.audit_agent("rust", critical)
        trend = auditor.get_agent_trend("rust")
        assert trend["trend"] == "degrading"

    def test_trend_improving(self):
        auditor = AgentAuditor()
        # First 3 audits: critical
        critical = {"messages_received": 50, "messages_sent": 50, "errors": 30}
        for _ in range(3):
            auditor.audit_agent("volt", critical)
        # Last 2 audits: healthy
        healthy = {"messages_received": 50, "messages_sent": 50, "errors": 1}
        for _ in range(2):
            auditor.audit_agent("volt", healthy)
        trend = auditor.get_agent_trend("volt")
        assert trend["trend"] == "improving"


# ---------------------------------------------------------------------------
# Audit log integration tests
# ---------------------------------------------------------------------------

class TestAuditLogIntegration:
    """Test that audit results are logged correctly."""

    def test_successful_audit_logged(self):
        audit_log = MagicMock()
        auditor = AgentAuditor(audit_log=audit_log)
        status = {"messages_received": 50, "messages_sent": 50, "errors": 1}
        auditor.audit_agent("volt", status)
        audit_log.log.assert_called_once()
        call_kwargs = audit_log.log.call_args[1]
        assert call_kwargs["action"] == "agent_audit"
        assert call_kwargs["success"] is True

    def test_critical_audit_logged_as_failure(self):
        audit_log = MagicMock()
        auditor = AgentAuditor(audit_log=audit_log)
        status = {"messages_received": 50, "messages_sent": 50, "errors": 30}
        auditor.audit_agent("volt", status)
        call_kwargs = audit_log.log.call_args[1]
        assert call_kwargs["success"] is False

    def test_last_audit_timestamp_updated(self):
        auditor = AgentAuditor()
        status = {"messages_received": 10, "messages_sent": 10, "errors": 0}
        auditor.audit_agent("volt", status)
        assert "volt" in auditor._last_audit
        assert auditor._last_audit["volt"] > 0
