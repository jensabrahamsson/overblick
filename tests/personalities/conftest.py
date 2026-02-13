"""
Test fixtures for personality LLM scenario tests.

These tests use the REAL local Ollama LLM (qwen3:8b) to validate that
personality prompts produce in-character responses. They are marked with
@pytest.mark.llm and skipped if Ollama is not running.

Purpose: prompt engineering validation — iterate on personality YAML until
the LLM consistently stays in character.
"""

import logging

import pytest

from overblick.core.llm.ollama_client import OllamaClient
from overblick.personalities import build_system_prompt, load_personality, list_personalities

logger = logging.getLogger(__name__)

# Cache the health check result across tests (module-level flag)
_ollama_available: bool | None = None


@pytest.fixture
async def ollama_client():
    """
    Per-test Ollama client.

    Uses qwen3:8b. Skips all tests if Ollama is not running.
    Each test gets a fresh client to avoid event loop conflicts.
    """
    global _ollama_available

    client = OllamaClient(
        model="qwen3:8b",
        temperature=0.7,
        max_tokens=500,
        timeout_seconds=120,
    )

    # Only check health once per session
    if _ollama_available is None:
        _ollama_available = await client.health_check()

    if not _ollama_available:
        await client.close()
        pytest.skip("Ollama not running or qwen3:8b not available")

    yield client
    await client.close()


async def generate_response(
    client: OllamaClient,
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
    assert result is not None, "LLM returned None — check Ollama connectivity"
    content = result.get("content", "")
    assert content, "LLM returned empty content"
    return content


@pytest.fixture
def all_personality_names():
    """All available personality names in the stable."""
    return list_personalities()
