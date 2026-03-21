"""
Conftest for LLM client tests.

Sets OVERBLICK_ALLOW_DIRECT_LLM=1 so that OllamaClient and GatewayClient
can be instantiated directly in tests (bypassing the security check that
prevents plugins from creating their own LLM clients).
"""


import pytest


@pytest.fixture(autouse=True)
def allow_direct_llm(monkeypatch):
    """Allow direct LLM client instantiation in all LLM tests."""
    monkeypatch.setenv("OVERBLICK_ALLOW_DIRECT_LLM", "1")
