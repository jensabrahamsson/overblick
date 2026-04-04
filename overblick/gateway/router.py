"""
Request router for intelligent multi-backend routing.

Routes requests to the best available backend based on:
1. Explicit backend override (?backend=)
2. Complexity level:
   - einstein → deepseek only (uses deepseek-reasoner model, no fallback)
   - ultra → deepseek/remote (best available)
   - high → remote/deepseek (prefer remote)
   - low → local
3. Priority level (high → first non-local in backend_preference)
4. Default → first available in backend_preference chain

The router never fails — it always falls back to the default backend.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .backend_registry import BackendRegistry

logger = logging.getLogger(__name__)


class RequestRouter:
    """Routes requests to the best available backend.

    Supports round-robin across same-tier backends (e.g., remote + remote2)
    for load balancing when multiple GPU servers are available.
    """

    def __init__(self, registry: "BackendRegistry"):
        self._registry = registry
        self._round_robin_counter = 0

    def resolve_backend(
        self,
        priority: str | None = None,
        complexity: str | None = None,
        explicit_backend: str | None = None,
        exclude: set[str] | None = None,
    ) -> str:
        """
        Determine which backend should handle a request.

        Precedence:
        1. explicit_backend — user override, highest priority
        2a-i. complexity=einstein → deepseek only (reasoner model, no fallback)
        2a-ii. complexity=ultra → deepseek > remote > local
        2b. complexity=high → remote > deepseek > local
        3. complexity=low → local
        4. priority=high → first non-local in backend_preference
        5. Default → first available in backend_preference chain

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
                logger.debug("Router: explicit backend '%s'", explicit_backend)
                return explicit_backend
            all_backends = set(self._registry.available_backends)
            if explicit_backend not in all_backends:
                raise ValueError(
                    f"Backend '{explicit_backend}' not configured. "
                    f"Available: {', '.join(sorted(all_backends))}"
                )
            # Backend exists but is excluded (unhealthy) — fall back
            logger.warning(
                "Router: explicit backend '%s' excluded (unhealthy), falling back to default",
                explicit_backend,
            )
            return self._registry.default_backend

        # 2. Complexity-based routing

        # 2a-i. Einstein: prefer Gemma for deep reasoning, fallback to DeepSeek reasoner
        # Gemma is faster for most analysis tasks. DeepSeek reasoner is reserved
        # for cases where Gemma fails or is unavailable.
        if complexity == "einstein":
            for candidate in ("remote", "deepseek"):
                if candidate in available:
                    logger.info("Router: complexity=einstein → '%s'", candidate)
                    return candidate
            logger.warning(
                "Router: complexity=einstein but no remote/deepseek available — "
                "falling back to default"
            )
            return self._registry.default_backend

        # 2a-ii. Ultra: prefer deepseek for precision tasks (math, challenges)
        if complexity == "ultra":
            for candidate in ("deepseek", "remote"):
                if candidate in available:
                    logger.debug("Router: complexity=ultra → '%s'", candidate)
                    return candidate
            logger.debug("Router: complexity=ultra but no deepseek/remote, using default")
            return self._registry.default_backend

        # 2b. High: prefer Gemma (remote) for complex analysis tasks
        if complexity == "high":
            for candidate in ("remote", "remote2"):
                if candidate in available:
                    logger.debug("Router: complexity=high → '%s'", candidate)
                    return candidate
            logger.debug("Router: complexity=high but no remote/remote2, using default")
            return self._registry.default_backend

        if complexity == "low":
            if "local" in available:
                logger.debug("Router: complexity=low → 'local'")
                return "local"
            return self._registry.default_backend

        # 3. Priority-based routing — prefer Mistral (remote2) for fast responses
        # Gemma (remote) is slow due to reasoning tokens — reserve for complex tasks
        if priority == "high":
            # Prefer remote2 (Mistral) for speed, fallback to remote (Gemma)
            for candidate in ("remote2", "remote"):
                if candidate in available and candidate != "local" and candidate != "deepseek":
                    logger.debug("Router: priority=high → '%s' (prefer fast backend)", candidate)
                    return candidate
            # No remote available — fall through to preference chain
            for candidate in self._registry.backend_preference:
                if candidate in available and candidate != "local":
                    return candidate

        # 4. Default: prefer Mistral (remote2) for speed
        # Gemma (remote) is reserved for complexity=high tasks only
        if "remote2" in available:
            logger.debug("Router: default → 'remote2' (Mistral, fast)")
            return "remote2"

        for candidate in self._registry.backend_preference:
            if candidate in available:
                logger.debug("Router: default → '%s' (from preference)", candidate)
                return candidate
        return self._registry.default_backend
