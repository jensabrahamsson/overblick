"""Shared fixtures for capability tests.

Explicitly imports only the needed fixtures from moltbook conftest
instead of wildcard import.
"""

from tests.plugins.moltbook.conftest import (  # noqa: F401
    anomal_identity,
    cherry_identity,
    mock_moltbook_client,
    mock_llm_pipeline,
    anomal_plugin_context,
    cherry_plugin_context,
    setup_anomal_plugin,
    setup_cherry_plugin,
    make_post,
)
