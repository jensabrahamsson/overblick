from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from overblick.core.database.sqlite_backend import SQLiteBackend
from overblick.core.db.engagement_db import EngagementDB
from overblick.core.event_bus import EventBus
from overblick.core.learning.store import LearningStore
from overblick.core.llm.client import LLMClient
from overblick.core.llm.pipeline import SafeLLMPipeline
from overblick.core.permissions import PermissionChecker
from overblick.core.plugin_capability_checker import PluginCapabilityChecker
from overblick.core.capability import CapabilityRegistry
from overblick.core.plugin_registry import PluginRegistry
from overblick.core.quiet_hours import QuietHoursChecker
from overblick.core.scheduler import Scheduler
from overblick.core.security.audit_log import AuditLog
from overblick.core.security.output_safety import OutputSafety
from overblick.core.security.policy_gate import PolicyGate
from overblick.core.security.preflight import PreflightChecker
from overblick.core.security.rate_limiter import RateLimiter
from overblick.core.security.secrets_manager import SecretsManager
from overblick.supervisor.ipc import IPCClient
from overblick.identities import Identity


@dataclass
class OrchestratorServices:
    identity: Identity | None = None
    event_bus: EventBus = field(default_factory=EventBus)
    scheduler: Scheduler = field(default_factory=Scheduler)
    registry: PluginRegistry = field(default_factory=PluginRegistry)
    capability_registry: CapabilityRegistry | None = None

    audit_log: AuditLog | None = None
    secrets: SecretsManager | None = None
    quiet_hours: QuietHoursChecker | None = None

    llm_client: LLMClient | None = None
    llm_pipeline: SafeLLMPipeline | None = None
    preflight: PreflightChecker | None = None
    output_safety: OutputSafety | None = None
    rate_limiter: RateLimiter | None = None

    permissions: PermissionChecker | None = None
    capability_checker: PluginCapabilityChecker | None = None
    policy_gate: PolicyGate | None = None

    ipc_client: IPCClient | None = None
    engagement_db_backend: SQLiteBackend | None = None
    engagement_db: EngagementDB | None = None
    learning_store: LearningStore | None = None
