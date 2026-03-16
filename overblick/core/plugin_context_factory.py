from __future__ import annotations

from pathlib import Path
from typing import Any

from overblick.core.permissions import PermissionChecker
from overblick.core.plugin_base import (
    AgenticPluginContext,
    CommunicationPluginContext,
    ContentPluginContext,
    DefaultPluginContext,
    MonitoringPluginContext,
    PluginContext,
)
from overblick.core.plugin_capability_checker import PluginCapabilityChecker
from overblick.core.orchestrator_paths import OrchestratorPaths
from overblick.core.orchestrator_services import OrchestratorServices
from overblick.identities import Identity


class PluginContextFactory:
    def __init__(
        self,
        *,
        identity_name: str,
        services: OrchestratorServices,
        paths: OrchestratorPaths,
    ) -> None:
        self._identity_name = identity_name
        self._services = services
        self._paths = paths

    def create(
        self,
        *,
        plugin_name: str,
        permissions: PermissionChecker,
        capability_checker: PluginCapabilityChecker,
    ) -> PluginContext:
        """
        Create a role-specific PluginContext for a plugin.

        Uses _PLUGIN_ROLES mapping to select appropriate context class.
        Falls back to DefaultPluginContext for unclassified plugins.
        """
        assert self._services.secrets is not None, "Secrets manager must be initialized"
        assert self._services.audit_log is not None, "Audit log must be initialized"

        # Use same mapping as before
        plugin_roles = {
            # Agentic plugins
            "github": AgenticPluginContext,
            "dev_agent": AgenticPluginContext,
            "log_agent": AgenticPluginContext,
            # Communication plugins
            "telegram": CommunicationPluginContext,
            "email_agent": CommunicationPluginContext,
            "irc": CommunicationPluginContext,
            # Content plugins
            "moltbook": ContentPluginContext,
            "kontrast": ContentPluginContext,
            "skuggspel": ContentPluginContext,
            "spegel": ContentPluginContext,
            "ai_digest": ContentPluginContext,
            # Monitoring plugins
            "host_health": MonitoringPluginContext,
            "compass": MonitoringPluginContext,
            "stage": MonitoringPluginContext,
        }

        context_class = plugin_roles.get(plugin_name, DefaultPluginContext)

        # All context classes have the same constructor signature
        ctx = context_class(
            identity_name=self._identity_name,
            data_dir=self._paths.data_dir,
            log_dir=self._paths.log_dir,
            event_bus=self._services.event_bus,
            scheduler=self._services.scheduler,
            audit_log=self._services.audit_log,
            quiet_hours_checker=self._services.quiet_hours,
            llm_pipeline=self._services.llm_pipeline,
            identity=self._services.identity,
            preflight=self._services.preflight,
            output_safety=self._services.output_safety,
            rate_limiter=self._services.rate_limiter,
            permissions=permissions,
            policy_gate=self._services.policy_gate,
            ipc_client=self._services.ipc_client,
            engagement_db=self._services.engagement_db,
            learning_store=self._services.learning_store,
            get_secret=self._services.secrets.get_secret
            if self._services.secrets
            else lambda x: None,
        )
        return ctx
