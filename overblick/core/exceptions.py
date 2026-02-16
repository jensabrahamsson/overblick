"""
Framework exception hierarchy for Överblick.

All framework-specific exceptions inherit from OverblickError so callers
can catch broad or narrow error types as needed.

Hierarchy:
    OverblickError
    ├── ConfigError          — invalid/missing configuration
    ├── SecurityError        — security policy violations
    ├── PluginError          — plugin lifecycle failures
    ├── LLMError             — LLM communication failures
    │   ├── LLMTimeoutError  — request timeout
    │   └── LLMConnectionError — connection failure
    └── DatabaseError        — database operation failures
"""


class OverblickError(Exception):
    """Base exception for all Överblick framework errors."""


class ConfigError(OverblickError):
    """Invalid or missing configuration (e.g. unresolved placeholders)."""


class SecurityError(OverblickError):
    """Security policy violation (e.g. injection attempt, auth failure)."""


class PluginError(OverblickError):
    """Plugin lifecycle failure (setup, tick, teardown)."""


class LLMError(OverblickError):
    """LLM communication failure."""


class LLMTimeoutError(LLMError):
    """LLM request timed out."""


class LLMConnectionError(LLMError):
    """Failed to connect to LLM backend."""


class DatabaseError(OverblickError):
    """Database operation failure."""
