"""
Centralized security settings for Överblick.

Defines environment variable defaults and safe‑by‑default behavior for
the entire framework. All security‑sensitive configuration should be
imported from this module — never read environment variables directly.

Constants:
    SAFE_MODE (bool): Enable fail‑closed SafeLLMPipeline (default True).
    RAW_LLM (bool): Allow plugins direct access to ctx.llm_client (default False).
    STRICT_CAPABILITIES (bool): Enforce capability grants (default False).
"""

import os


def _env_bool(name: str, default: bool) -> bool:
    """Parse boolean environment variable (1, true, yes, on → True)."""
    val = os.getenv(name)
    if val is None:
        return default
    val = val.lower()
    return val in ("1", "true", "yes", "on")


# ── Safe‑by‑default mode ──────────────────────────────────────────────────────

SAFE_MODE = _env_bool("OVERBLICK_SAFE_MODE", default=True)
"""Enable fail‑closed SafeLLMPipeline (default True).

When True:
    • SafeLLMPipeline runs with strict=True (requires preflight, output safety, rate limiting).
    • Missing security components cause pipeline to raise ConfigError.
    • skip_preflight and skip_output_safety are only allowed for trusted internal content.

When False:
    • SafeLLMPipeline runs with strict=False (components optional).
    • Missing security components are logged as warnings but do not block requests.
    • skip_preflight and skip_output_safety can be used more permissively.

Set OVERBLICK_SAFE_MODE=0 to disable safe‑by‑default (not recommended for production).
"""


# ── Raw LLM client access ─────────────────────────────────────────────────────

RAW_LLM = _env_bool("OVERBLICK_RAW_LLM", default=False)
"""Allow plugins direct access to ctx.llm_client (default False).

When False:
    • PluginContext.llm_client raises RuntimeError.
    • Plugins must use PluginContext.llm_pipeline for all LLM calls.
    • Prevents plugins from bypassing SafeLLMPipeline security.

When True:
    • PluginContext.llm_client returns the raw LLM client (OllamaClient, etc.).
    • Only for internal/debug use — never expose to untrusted plugins.

Set OVERBLICK_RAW_LLM=1 to enable raw client access (not recommended for production).
"""


# ── Strict capability enforcement ─────────────────────────────────────────────

STRICT_CAPABILITIES = _env_bool("OVERBLICK_STRICT_CAPABILITIES", default=False)
"""Enforce capability grants at plugin load time (default False).

When False:
    • Missing capability grants are logged as warnings.
    • Plugins still load; capabilities may fail at runtime.

When True:
    • Missing capability grants raise PermissionError during plugin setup.
    • Plugins cannot start without the required capability grants.
    • Recommended for production deployments.

Set OVERBLICK_STRICT_CAPABILITIES=1 to enforce capability grants.
"""


# ── Convenience helpers ───────────────────────────────────────────────────────


def is_production() -> bool:
    """Return True if safe‑by‑default mode is enabled (production default)."""
    return SAFE_MODE


def allow_raw_access() -> bool:
    """Return True if raw LLM client access is permitted (debug only)."""
    return RAW_LLM


def enforce_capabilities() -> bool:
    """Return True if capability grants are enforced."""
    return _env_bool("OVERBLICK_STRICT_CAPABILITIES", default=False)


def safe_mode() -> bool:
    """Return safe‑by‑default mode (reads environment variable each call)."""
    return _env_bool("OVERBLICK_SAFE_MODE", default=True)


def raw_llm() -> bool:
    """Return raw LLM client access permission (reads environment variable each call)."""
    return _env_bool("OVERBLICK_RAW_LLM", default=False)


# Cache for strict capabilities check
_strict_capabilities_cache: bool | None = None


def strict_capabilities() -> bool:
    """Return strict capability enforcement (cached after first read).

    Caches the value to avoid repeated environment variable reads.
    Only changes when Python process restarts (not during runtime).
    """
    global _strict_capabilities_cache
    if _strict_capabilities_cache is None:
        _strict_capabilities_cache = _env_bool("OVERBLICK_STRICT_CAPABILITIES", default=False)
    return _strict_capabilities_cache


def reset_strict_capabilities() -> None:
    """Reset strict capabilities cache (useful for tests)."""
    global _strict_capabilities_cache
    _strict_capabilities_cache = None
