"""
YAML-driven multi-turn conversation tests for personalities.

Each personality can have a conversations/<name>_conversations.yaml file
defining multi-turn scripts with per-turn assertions.

Run with:
    pytest tests/personalities/test_multi_turn_conversations.py -v -s -m llm_slow

Tests are marked @pytest.mark.llm_slow (longer running than single-turn).
"""

import logging

import pytest

from overblick.identities import build_system_prompt, load_personality, list_personalities
from tests.personalities.conftest import MODEL_SLUG
from tests.personalities.helpers import (
    apply_scenario_result,
    check_assertions,
    load_conversations,
)

logger = logging.getLogger(__name__)

_MAX_CONVERSATION_RETRIES = 2  # Retry entire conversation up to twice on failure

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
        """Run a multi-turn conversation, checking assertions per turn.

        Retries the entire conversation up to _MAX_CONVERSATION_RETRIES times
        on hard failures to account for LLM non-determinism.
        """
        personality = load_personality(personality_name)
        prompt = build_system_prompt(personality, model_slug=MODEL_SLUG)

        last_result = None
        last_response = ""

        for attempt in range(_MAX_CONVERSATION_RETRIES + 1):
            messages = [{"role": "system", "content": prompt}]
            failed = False

            for turn_idx, turn in enumerate(conversation.get("turns", [])):
                user_content = turn["content"]
                messages.append({"role": "user", "content": user_content})

                result = await ollama_client.chat(messages=messages)
                content = result.get("content", "") if result else ""
                if not content:
                    # Empty content (e.g. Qwen3 think-token stripping) — retry
                    last_response = (
                        f"[turn {turn_idx + 1}] <empty — LLM returned no content>"
                    )
                    failed = True
                    break

                messages.append({"role": "assistant", "content": content})

                assertions = turn.get("assertions", {})
                if assertions:
                    scenario_result = check_assertions(content, assertions, personality)
                    if not scenario_result.passed and not scenario_result.is_soft_failure:
                        last_result = scenario_result
                        last_response = f"[turn {turn_idx + 1}] {content}"
                        failed = True
                        break
                    elif not scenario_result.passed:
                        # Soft failure — warn but continue
                        last_result = scenario_result
                        last_response = f"[turn {turn_idx + 1}] {content}"

            if not failed:
                # All turns passed (or only soft failures)
                if last_result and not last_result.passed:
                    apply_scenario_result(last_result, last_response)
                return

            if attempt < _MAX_CONVERSATION_RETRIES:
                logger.warning(
                    "Conversation %s-%s: assertion failed (attempt %d/%d), retrying...",
                    personality_name,
                    conversation["id"],
                    attempt + 1,
                    _MAX_CONVERSATION_RETRIES + 1,
                )

        # All retries exhausted
        if last_result:
            apply_scenario_result(last_result, last_response)
