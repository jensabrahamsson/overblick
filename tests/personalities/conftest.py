"""
Test fixtures for personality LLM scenario tests.

These tests use the LLM Gateway (port 8200) which provides priority queuing
for local Ollama inference. The gateway must be running:

    python -m overblick.gateway

Tests are marked with @pytest.mark.llm and skipped if the gateway is not running.

Set OVERBLICK_TEST_MODEL to select a model (default: qwen3_8b). This controls:
- Which scenario directory is used (tests/personalities/scenarios/<model>/)
- Which LLM hints are loaded (overblick/personalities/<name>/llm_hints/<model>.yaml)

Purpose: prompt engineering validation — iterate on personality YAML until
the LLM consistently stays in character.
"""

import logging
import os

import pytest

from overblick.core.llm.gateway_client import GatewayClient
from overblick.personalities import build_system_prompt, load_personality, list_personalities

logger = logging.getLogger(__name__)

# Model slug used for LLM-specific scenarios and prompt hints
MODEL_SLUG = os.environ.get("OVERBLICK_TEST_MODEL", "qwen3_8b")

# Cache the health check result across tests (module-level flag)
_gateway_available: bool | None = None


@pytest.fixture
async def ollama_client():
    """
    Per-test LLM client via the Gateway.

    Uses qwen3:8b through the LLM Gateway on port 8200.
    Skips all tests if the gateway is not running.
    Each test gets a fresh client to avoid event loop conflicts.

    Note: Fixture is still named 'ollama_client' for backward compatibility
    with existing tests, but uses GatewayClient under the hood.
    """
    global _gateway_available

    client = GatewayClient(
        base_url="http://127.0.0.1:8200",
        model="qwen3:8b",
        default_priority="low",
        temperature=0.7,
        max_tokens=500,
        timeout_seconds=180,
    )

    # Only check health once per session
    if _gateway_available is None:
        _gateway_available = await client.health_check()

    if not _gateway_available:
        await client.close()
        pytest.skip("LLM Gateway not running (start with: python -m overblick.gateway)")

    yield client
    await client.close()


async def generate_response(
    client: GatewayClient,
    system_prompt: str,
    user_message: str,
) -> str:
    """
    Send a message to the LLM with the given system prompt.

    Returns the response content string, or raises if the LLM fails.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    result = await client.chat(messages=messages)
    assert result is not None, "LLM returned None — check Gateway connectivity"
    content = result.get("content", "")
    assert content, "LLM returned empty content"
    return content


@pytest.fixture
def all_personality_names():
    """All available personality names in the stable."""
    return list_personalities()
