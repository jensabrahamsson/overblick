"""
Centralized security settings for Överblick.

Defines environment variable defaults and safe‑by‑default behavior for
the entire framework. All security‑sensitive configuration should be
imported from this module — never read environment variables directly.

Constants:
    SAFE_MODE (bool): Enable fail‑closed SafeLLMPipeline (default True).
    RAW_LLM (bool): Allow plugins direct access to ctx.llm_client (default False).
    STRICT_CAPABILITIES (bool): Enforce capability grants (default False).
    ALLOW_RAW_FALLBACK (bool): Allow raw LLM fallback when pipeline fails (default False).
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
    • Missing security components cause pipeline to raise SecurityError.
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
    • PluginContext.llm_client raises AttributeError.
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


# ── Raw LLM fallback ──────────────────────────────────────────────────────────

ALLOW_RAW_FALLBACK = _env_bool("OVERBLICK_ALLOW_RAW_FALLBACK", default=False)
"""Allow raw LLM fallback when SafeLLMPipeline fails (default False).

When True:
    • If SafeLLMPipeline raises SecurityError, the call may fall back to raw LLM client.
    • Used only in trusted supervisor handlers (health, email, research).
    • Never exposed to untrusted plugin code.

When False:
    • Pipeline failures propagate as SecurityError (fail‑closed).
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
    return STRICT_CAPABILITIES
