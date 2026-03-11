"""Shared fixtures for capability tests.

Explicitly imports only the needed fixtures from moltbook conftest
instead of wildcard import.
"""

from tests.plugins.moltbook.conftest import (  # noqa: F401
    anomal_identity,
    anomal_plugin_context,
    cherry_identity,
    cherry_plugin_context,
    make_post,
    mock_llm_pipeline,
    mock_moltbook_client,
    setup_anomal_plugin,
    setup_cherry_plugin,
)
