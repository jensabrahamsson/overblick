"""
Shared onboarding chat logic for Setup Wizard and Dashboard Onboarding.

Provides adaptive LLM connectivity (Gateway -> Ollama fallback) and
onboarding-specific prompt injection for identity test conversations.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def chat_with_identity(
    identity_name: str,
    message: str,
    llm_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Send a chat message to an identity during onboarding.

    Uses adaptive LLM backend: Gateway first, Ollama fallback.

    Args:
        identity_name: Name of the identity to chat with.
        message: User's message.
        llm_config: LLM configuration dict (model, temperature, etc.).

    Returns:
        dict with 'response', 'identity', 'success', and optionally 'error'.
    """
    if not llm_config:
        llm_config = {}

    model = llm_config.get("model", "qwen3:8b")
    temperature = float(llm_config.get("temperature", 0.7))
    max_tokens = int(llm_config.get("max_tokens", 500))

    # Build system prompt
    system_prompt = build_onboarding_prompt(identity_name)
    if not system_prompt:
        return {
            "response": "",
            "identity": identity_name,
            "success": False,
            "error": f"Identity '{identity_name}' not found.",
        }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message},
    ]

    # Try adaptive LLM: Gateway -> Ollama
    try:
        result = await _call_llm(messages, model, temperature, max_tokens, llm_config)
        if result:
            return {
                "response": result,
                "identity": identity_name,
                "success": True,
            }
    except Exception as e:
        logger.warning("Onboarding chat failed: %s", e)
        return {
            "response": "",
            "identity": identity_name,
            "success": False,
            "error": str(e),
        }

    return {
        "response": "",
        "identity": identity_name,
        "success": False,
        "error": "No LLM backend available.",
    }


async def test_llm_connection(llm_config: dict[str, Any]) -> dict[str, Any]:
    """
    Test LLM connectivity with a simple prompt.

    Returns dict with 'success', 'provider' (which backend responded), and 'error'.
    """
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Respond in one short sentence."},
        {"role": "user", "content": "Say hello."},
    ]

    model = llm_config.get("model", "qwen3:8b")

    try:
        result = await _call_llm(messages, model, 0.5, 100, llm_config)
        if result:
            return {"success": True, "provider": "llm", "response": result}
    except Exception as e:
        return {"success": False, "error": str(e)}

    return {"success": False, "error": "No LLM backend available."}


def build_onboarding_prompt(identity_name: str) -> str | None:
    """
    Build a system prompt for onboarding chat.

    Loads the identity's base prompt and injects onboarding context.
    """
    try:
        from overblick.identities import load_identity, build_system_prompt
        identity = load_identity(identity_name)
        base_prompt = build_system_prompt(identity, platform="onboarding")
    except Exception as e:
        logger.warning("Failed to load identity '%s': %s", identity_name, e)
        return None

    onboarding_context = (
        "\n\n=== ONBOARDING CONTEXT ===\n"
        "You are being introduced during the Överblick setup process. "
        "The user is getting to know you and testing the system.\n"
        "You can explain:\n"
        "- Your personality, interests, and communication style\n"
        "- What Överblick is (a multi-identity agent framework)\n"
        "- Your role within the identity stable\n"
        "- How you interact with other identities\n"
        "Keep responses concise (2-3 sentences). Be yourself — show your personality."
    )

    return base_prompt + onboarding_context


async def _call_llm(
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    llm_config: dict[str, Any],
) -> str | None:
    """Adaptive LLM call: try Gateway, fall back to Ollama."""
    provider = llm_config.get("provider", "ollama")
    gateway_url = llm_config.get("gateway_url", "http://127.0.0.1:8200")

    # Try Gateway first (unless provider is explicitly ollama-only)
    if provider in ("gateway", "ollama"):
        try:
            result = await _call_gateway(messages, model, temperature, max_tokens, gateway_url)
            if result:
                return result
        except Exception:
            logger.debug("Gateway unavailable, falling back to Ollama")

    # Fallback: Ollama direct
    try:
        return await _call_ollama(messages, model, temperature, max_tokens, llm_config)
    except Exception as e:
        logger.warning("Ollama direct call failed: %s", e)
        raise


async def _call_gateway(
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    gateway_url: str,
) -> str | None:
    """Call the LLM Gateway (OpenAI-compatible)."""
    import aiohttp

    url = f"{gateway_url.rstrip('/')}/v1/chat/completions?priority=low"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                # Strip think tokens
                from overblick.core.llm.client import LLMClient
                return LLMClient.strip_think_tokens(content).strip()
    return None


async def _call_ollama(
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    llm_config: dict[str, Any],
) -> str | None:
    """Call Ollama directly (OpenAI-compatible endpoint)."""
    import aiohttp

    host = llm_config.get("ollama_host", "127.0.0.1")
    port = llm_config.get("ollama_port", "11434")
    url = f"http://{host}:{port}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ConnectionError(f"Ollama returned {resp.status}: {text[:200]}")
            data = await resp.json()
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                from overblick.core.llm.client import LLMClient
                return LLMClient.strip_think_tokens(content).strip()
    return None
