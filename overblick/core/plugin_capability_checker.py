"""
Plugin capability checking — minimal permission system for plugin resource access.

Plugins declare required capabilities (e.g., "network_outbound", "filesystem_write").
Users grant capabilities per identity and per plugin in identity YAML:

    plugin_capabilities:
      telegram:
        network_outbound: true
        secrets_access: true
      email_agent:
        email_send: true
        secrets_access: true

Missing grants trigger warnings in logs. For beta, plugins still load but capabilities
may fail at runtime. Set OVERBLICK_STRICT_CAPABILITIES=1 to raise PermissionError for
missing grants (recommended for production).
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class PluginCapabilityChecker:
    """
    Checks plugin capability grants against requirements.

    Minimal implementation for beta safety — logs warnings but doesn't block.
    Set OVERBLICK_STRICT_CAPABILITIES=1 to raise PermissionError for missing grants.
    Future: integrate with permission system for runtime checks.
    """

    # Standard capability definitions
    CAPABILITIES = {
        # Network access
        "network_outbound": "Make HTTP/HTTPS requests to external services",
        "network_inbound": "Accept incoming network connections",
        # Filesystem access
        "filesystem_read": "Read files outside plugin data directory",
        "filesystem_write": "Write files outside plugin data directory",
        # Process execution
        "shell_execute": "Execute shell commands or subprocesses",
        # Email
        "email_send": "Send emails via SMTP",
        "email_receive": "Receive emails (IMAP/POP3)",
        # Secrets
        "secrets_access": "Read secrets via ctx.get_secret()",
        # LLM resources
        "llm_high_priority": "Use high-priority LLM queue (gateway)",
        "llm_unlimited": "Bypass rate limits for LLM calls",
        # System resources
        "database_write": "Write to central database",
        "ipc_send": "Send IPC messages to other identities",
    }

    def __init__(self, identity_name: str, raw_config: dict[str, Any]):
        self.identity_name = identity_name
        self.grants = raw_config.get("plugin_capabilities", {})

    def check_plugin(self, plugin_name: str, required_capabilities: list[str]) -> bool:
        """
        Check if a plugin's required capabilities are granted.

        Args:
            plugin_name: Name of the plugin
            required_capabilities: List of capability strings required

        Returns:
            True if all required capabilities are granted (or warning logged)

        Raises:
            PermissionError: If OVERBLICK_STRICT_CAPABILITIES=1 and missing grants
        """
        if not required_capabilities:
            return True

        plugin_grants = self.grants.get(plugin_name, {})
        missing = []
        warned = []

        for cap in required_capabilities:
            if cap not in self.CAPABILITIES:
                logger.warning(
                    "Plugin '%s' requires unknown capability '%s' (identity: %s)",
                    plugin_name,
                    cap,
                    self.identity_name,
                )
                warned.append(cap)
                continue

            granted = plugin_grants.get(cap, False)
            if not granted:
                missing.append(cap)

        strict = os.environ.get("OVERBLICK_STRICT_CAPABILITIES", "0") == "1"
        if strict and (missing or warned):
            raise PermissionError(
                f"Plugin '{plugin_name}' missing capability grants: {missing}. "
                f"Unknown capabilities: {warned}. "
                f"Add to identity YAML under 'plugin_capabilities' section."
            )

        if missing:
            logger.warning(
                "Plugin '%s' missing capability grants: %s "
                "(identity: %s). Add to identity YAML:\n"
                "plugin_capabilities:\n"
                "  %s:\n%s",
                plugin_name,
                ", ".join(missing),
                self.identity_name,
                plugin_name,
                "\n".join(
                    f"    {cap}: true  # {self.CAPABILITIES[cap]}" for cap in missing
                ),
            )

        if warned:
            # Unknown capabilities - plugin might fail at runtime
            return False

        return len(missing) == 0
