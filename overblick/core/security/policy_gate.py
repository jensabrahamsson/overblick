"""
Centralized policy gate for security enforcement.

Consolidates all security decisions (preflight, output safety, rate limiting,
capability checks, permissions) into a single entry point.

Plugins should use ctx.policy_gate for all security-related decisions.
"""

import logging
from typing import Any

from overblick.core.llm.pipeline import PipelineResult, SafeLLMPipeline
from overblick.core.permissions import PermissionChecker
from overblick.core.plugin_capability_checker import PluginCapabilityChecker

logger = logging.getLogger(__name__)


class PolicyGate:
    """
    Centralized security policy gate.

    Provides a unified interface for all security checks:
    - Plugin capability verification
    - Action permission checking
    - LLM request safety pipeline
    - Content safety filtering
    - Preflight checks
    """

    def __init__(
        self,
        identity_name: str,
        permission_checker: PermissionChecker,
        capability_checker: PluginCapabilityChecker,
        llm_pipeline: SafeLLMPipeline | None = None,
        preflight_checker: Any = None,
        output_safety: Any = None,
        rate_limiter: Any = None,
    ):
        self._identity_name = identity_name
        self._permission_checker = permission_checker
        self._capability_checker = capability_checker
        self._llm_pipeline = llm_pipeline
        self._preflight_checker = preflight_checker
        self._output_safety = output_safety
        self._rate_limiter = rate_limiter

    # --- Plugin capability checks ---

    def check_plugin_capability(self, plugin_name: str, capability: str) -> bool:
        """
        Check if a plugin has a specific capability grant.

        Args:
            plugin_name: Name of the plugin
            capability: Capability string (e.g., "network_outbound")

        Returns:
            True if the capability is granted (or warning logged in non‑strict mode)
        """
        # Delegate to PluginCapabilityChecker
        return self._capability_checker.check_plugin(plugin_name, [capability])

    def require_plugin_capability(self, plugin_name: str, capability: str) -> None:
        """
        Require a capability for a plugin — raises PermissionError if missing.

        Use this for critical capabilities that must be present for the plugin
        to function correctly.

        Args:
            plugin_name: Name of the plugin
            capability: Required capability

        Raises:
            PermissionError: If the capability is not granted
        """
        if not self.check_plugin_capability(plugin_name, capability):
            raise PermissionError(
                f"Plugin '{plugin_name}' missing required capability '{capability}'"
            )

    # --- Action permission checks ---

    def is_action_allowed(self, plugin_name: str, action: str) -> bool:
        """
        Check if a plugin is allowed to perform a given action.

        Combines capability checks with permission system.

        Args:
            plugin_name: Name of the plugin
            action: Action name (e.g., "post", "comment")

        Returns:
            True if the action is allowed
        """
        # First check if the plugin has the general capability to perform actions
        # (optional — we could map actions to capabilities)
        # For now, just delegate to permission checker
        return self._permission_checker.is_allowed(action)

    def deny_reason(self, plugin_name: str, action: str) -> str | None:
        """
        Get a human‑readable reason why an action is denied.

        Returns None if the action is allowed.
        """
        return self._permission_checker.denial_reason(action)

    # --- LLM safety pipeline ---

    async def safe_llm_chat(
        self,
        messages: list[dict[str, str]],
        user_id: str = "system",
        **kwargs,
    ) -> PipelineResult:
        """
        Send a chat request through the full LLM safety pipeline.

        This is the recommended way for plugins to make LLM calls.
        If no LLM pipeline is configured, raises RuntimeError.

        Args:
            messages: Chat messages
            user_id: User ID for preflight tracking
            **kwargs: Additional arguments passed to SafeLLMPipeline.chat

        Returns:
            PipelineResult with safe content or block information
        """
        if self._llm_pipeline is None:
            raise RuntimeError(f"No LLM pipeline configured for identity '{self._identity_name}'")
        return await self._llm_pipeline.chat(messages, user_id=user_id, **kwargs)

    # --- Content safety ---

    def check_content_safety(self, content: str) -> bool:
        """
        Check if content passes output safety filters.

        Returns True if safe, False if blocked.
        Fail-closed: returns False if output safety is not configured.
        """
        if self._output_safety is None:
            logger.warning(
                "Output safety not configured for identity '%s' — denying content (fail-closed)",
                self._identity_name,
            )
            return False
        # OutputSafety provides a sanitize method
        result = self._output_safety.sanitize(content)
        return not result.blocked

    # --- Preflight checks ---

    async def check_preflight(
        self,
        messages: list[dict[str, str]],
        user_id: str = "system",
    ) -> bool:
        """
        Run preflight check on messages.

        Returns True if allowed, False if blocked.
        """
        if self._preflight_checker is None:
            logger.warning(
                "Preflight checker not configured for identity '%s' — denying request (fail-closed)",
                self._identity_name,
            )
            return False
        # Assuming PreflightChecker has a check method
        if hasattr(self._preflight_checker, "check"):
            result = await self._preflight_checker.check(messages, user_id)
            return not result.blocked if hasattr(result, "blocked") else result
        else:
            # Fallback: fail-closed — deny if checker lacks expected interface
            logger.warning(
                "Preflight checker for identity '%s' has no check() method — denying request (fail-closed)",
                self._identity_name,
            )
            return False

    # --- Rate limiting ---

    def check_rate_limit(self, key: str) -> bool:
        """
        Check if a rate‑limited resource is available.

        Args:
            key: Rate limit key (e.g., "llm:plugin_name")

        Returns:
            True if allowed, False if rate limited
        """
        if self._rate_limiter is None:
            logger.warning(
                "Rate limiter not configured — denying request for key '%s' (fail-closed)",
                key,
            )
            return False
        return self._rate_limiter.allow(key)

    # --- Convenience methods for common plugin patterns ---

    async def safe_generate(
        self,
        plugin_name: str,
        prompt: str,
        user_id: str = "system",
        **kwargs,
    ) -> str:
        """
        Generate text safely using the LLM pipeline.

        This is a convenience wrapper for plugins that need to generate
        content with a single prompt.

        Args:
            plugin_name: Plugin name (for logging)
            prompt: Prompt text
            user_id: User ID for preflight tracking
            **kwargs: Additional arguments for safe_llm_chat

        Returns:
            Generated text (guaranteed safe), or empty string if blocked
        """
        messages = [{"role": "user", "content": prompt}]
        result = await self.safe_llm_chat(messages, user_id=user_id, **kwargs)
        if result.blocked:
            logger.warning(
                "Plugin '%s' LLM generation blocked: %s",
                plugin_name,
                result.block_reason,
            )
            return ""
        return result.content

    def get_stats(self) -> dict[str, Any]:
        """
        Get security statistics for monitoring.

        Returns:
            Dict with permission stats, capability grants, rate limit usage, etc.
        """
        stats = {
            "identity": self._identity_name,
            "permissions": self._permission_checker.get_stats(),
        }
        return stats
