"""
Request router for intelligent multi-backend routing.

Routes requests to the best available backend based on:
1. Explicit backend override (?backend=)
2. Complexity level:
   - einstein → deepseek only (uses deepseek-reasoner model, no fallback)
   - ultra → deepseek/cloud (best available)
   - high → cloud/deepseek (prefer cloud)
   - low → local
3. Priority level (high + cloud available → cloud)
4. Default backend from configuration

The router never fails — it always falls back to the default backend.
"""

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .backend_registry import BackendRegistry

logger = logging.getLogger(__name__)


class RequestRouter:
    """Routes requests to the best available backend."""

    def __init__(self, registry: "BackendRegistry"):
        self._registry = registry

    def resolve_backend(
        self,
        priority: Optional[str] = None,
        complexity: Optional[str] = None,
        explicit_backend: Optional[str] = None,
        exclude: Optional[set[str]] = None,
    ) -> str:
        """
        Determine which backend should handle a request.

        Precedence:
        1. explicit_backend — user override, highest priority
        2a-i. complexity=einstein → deepseek only (reasoner model, no fallback)
        2a-ii. complexity=ultra → deepseek > cloud > local
        2b. complexity=high → cloud > deepseek > local
        3. complexity=low → local
        4. priority=high + cloud available → cloud (backward compat)
        5. Default → registry.default_backend

        Args:
            priority: Request priority ("high" or "low")
            complexity: Request complexity ("high" or "low")
            explicit_backend: Explicit backend name from ?backend= param
            exclude: Set of backend names to skip (e.g. unhealthy backends)

        Returns:
            Backend name to route to (always valid)
        """
        available = set(self._registry.available_backends)
        if exclude:
            available -= exclude

        # 1. Explicit backend override — always wins
        if explicit_backend:
            if explicit_backend in available:
                logger.debug(
                    "Router: explicit backend '%s'", explicit_backend
                )
                return explicit_backend
            all_backends = set(self._registry.available_backends)
            if explicit_backend not in all_backends:
                raise ValueError(
                    f"Backend '{explicit_backend}' not configured. "
                    f"Available: {', '.join(sorted(all_backends))}"
                )
            # Backend exists but is excluded (unhealthy) — fall back
            logger.warning(
                "Router: explicit backend '%s' excluded (unhealthy), "
                "falling back to default",
                explicit_backend,
            )
            return self._registry.default_backend

        # 2. Complexity-based routing

        # 2a-i. Einstein: deepseek-reasoner only — no fallback (reasoning is
        # DeepSeek-specific, other backends cannot run this model)
        if complexity == "einstein":
            if "deepseek" in available:
                logger.info("Router: complexity=einstein → 'deepseek' (reasoner mode)")
                return "deepseek"
            logger.warning(
                "Router: complexity=einstein but deepseek not available — "
                "falling back to default (reasoning will NOT be used)"
            )
            return self._registry.default_backend

        # 2a-ii. Ultra: prefer deepseek for precision tasks (math, challenges)
        if complexity == "ultra":
            for candidate in ("deepseek", "cloud"):
                if candidate in available:
                    logger.debug(
                        "Router: complexity=ultra → '%s'", candidate
                    )
                    return candidate
            logger.debug("Router: complexity=ultra but no deepseek/cloud, using default")
            return self._registry.default_backend

        # 2b. High: prefer cloud for complex tasks
        if complexity == "high":
            # Prefer cloud > deepseek > local
            for candidate in ("cloud", "deepseek"):
                if candidate in available:
                    logger.debug(
                        "Router: complexity=high → '%s'", candidate
                    )
                    return candidate
            logger.debug("Router: complexity=high but no cloud/deepseek, using default")
            return self._registry.default_backend

        if complexity == "low":
            if "local" in available:
                logger.debug("Router: complexity=low → 'local'")
                return "local"
            return self._registry.default_backend

        # 3. Priority-based routing (backward compatible, no complexity specified)
        if priority == "high" and "cloud" in available:
            logger.debug("Router: priority=high + cloud available → 'cloud'")
            return "cloud"

        # 4. Default
        return self._registry.default_backend
