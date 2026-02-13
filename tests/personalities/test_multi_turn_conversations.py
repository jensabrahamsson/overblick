"""
YAML-driven multi-turn conversation tests for personalities.

Each personality can have a conversations/<name>_conversations.yaml file
defining multi-turn scripts with per-turn assertions.

Run with:
    pytest tests/personalities/test_multi_turn_conversations.py -v -s -m llm_slow

Tests are marked @pytest.mark.llm_slow (longer running than single-turn).
"""

import pytest

from overblick.personalities import build_system_prompt, load_personality, list_personalities
from tests.personalities.conftest import MODEL_SLUG
from tests.personalities.helpers import (
    apply_scenario_result,
    check_assertions,
    load_conversations,
)

# Discover which personalities have conversation files
_CONVERSATIONS: list[tuple[str, dict]] = []
for _name in list_personalities():
    try:
        _convos = load_conversations(_name)
        for _convo in _convos:
            _CONVERSATIONS.append((_name, _convo))
    except FileNotFoundError:
        pass


@pytest.mark.llm
@pytest.mark.llm_slow
class TestMultiTurnConversations:
    """YAML-driven multi-turn conversation tests."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "personality_name,conversation",
        _CONVERSATIONS,
        ids=[f"{name}-{c['id']}" for name, c in _CONVERSATIONS],
    )
    async def test_conversation(self, ollama_client, personality_name, conversation):
        """Run a multi-turn conversation, checking assertions per turn."""
        personality = load_personality(personality_name)
        prompt = build_system_prompt(personality, model_slug=MODEL_SLUG)

        messages = [{"role": "system", "content": prompt}]

        for turn_idx, turn in enumerate(conversation.get("turns", [])):
            user_content = turn["content"]
            messages.append({"role": "user", "content": user_content})

            result = await ollama_client.chat(messages=messages)
            assert result is not None, (
                f"LLM returned None on turn {turn_idx + 1} of "
                f"{conversation['id']}"
            )
            content = result.get("content", "")
            assert content, (
                f"LLM returned empty content on turn {turn_idx + 1} of "
                f"{conversation['id']}"
            )

            messages.append({"role": "assistant", "content": content})

            assertions = turn.get("assertions", {})
            if assertions:
                scenario_result = check_assertions(content, assertions, personality)
                if not scenario_result.passed:
                    apply_scenario_result(
                        scenario_result,
                        f"[turn {turn_idx + 1}] {content}",
                    )
