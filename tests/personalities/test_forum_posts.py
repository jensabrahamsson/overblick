"""
YAML-driven forum post scenario tests with anti-repetition checks.

Tests that personalities generate appropriate, varied responses to forum
posts. Each personality can have a forum_posts/<name>_posts.yaml file.

Run with:
    pytest tests/personalities/test_forum_posts.py -v -s -m llm_slow

Tests are marked @pytest.mark.llm_slow (multiple LLM calls per test).
"""

import logging

import pytest

from overblick.identities import build_system_prompt, load_personality, list_personalities
from tests.personalities.conftest import MODEL_SLUG, generate_response
from tests.personalities.helpers import (
    apply_scenario_result,
    check_assertions,
    jaccard_similarity,
    load_forum_posts,
)

logger = logging.getLogger(__name__)

_MAX_ASSERTION_RETRIES = 2

# Discover which personalities have forum post files
_FORUM_POSTS: list[tuple[str, dict]] = []
for _name in list_personalities():
    try:
        _posts = load_forum_posts(_name)
        for _post in _posts:
            _FORUM_POSTS.append((_name, _post))
    except FileNotFoundError:
        pass


@pytest.mark.llm
@pytest.mark.llm_slow
class TestForumPosts:
    """YAML-driven forum post response tests."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "personality_name,post_spec",
        _FORUM_POSTS,
        ids=[f"{name}-{p['id']}" for name, p in _FORUM_POSTS],
    )
    async def test_forum_post_response(self, ollama_client, personality_name, post_spec):
        """Generate a response to a forum post and check assertions.

        Retries up to _MAX_ASSERTION_RETRIES times on hard failures to
        account for LLM non-determinism (e.g. ironic banned-word usage).
        """
        personality = load_personality(personality_name)
        prompt = build_system_prompt(personality, model_slug=MODEL_SLUG)

        user_message = (
            f"You see this post on a forum. Write a response in character:\n\n"
            f"{post_spec['post_content']}"
        )

        for attempt in range(_MAX_ASSERTION_RETRIES + 1):
            response = await generate_response(ollama_client, prompt, user_message)

            assertions = post_spec.get("assertions", {})
            result = check_assertions(response, assertions, personality)

            if result.passed or result.is_soft_failure:
                break

            if attempt < _MAX_ASSERTION_RETRIES:
                logger.warning(
                    "Forum post %s-%s: assertion failed (attempt %d/%d), retrying...",
                    personality_name,
                    post_spec.get("id", "?"),
                    attempt + 1,
                    _MAX_ASSERTION_RETRIES + 1,
                )

        apply_scenario_result(result, response)


@pytest.mark.llm
@pytest.mark.llm_slow
class TestForumPostVariety:
    """Test that forum post responses have variety (anti-repetition)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "personality_name",
        [name for name in list_personalities()
         if any(n == name for n, _ in _FORUM_POSTS)],
    )
    async def test_response_variety(self, ollama_client, personality_name):
        """Generate 3 responses to the same post, check Jaccard similarity < 0.5."""
        posts = load_forum_posts(personality_name)
        if not posts:
            pytest.skip(f"No forum posts for {personality_name}")

        post_spec = posts[0]  # Use the first post for variety check
        personality = load_personality(personality_name)
        prompt = build_system_prompt(personality, model_slug=MODEL_SLUG)

        user_message = (
            f"You see this post on a forum. Write a response in character:\n\n"
            f"{post_spec['post_content']}"
        )

        responses: list[str] = []
        for _ in range(3):
            response = await generate_response(ollama_client, prompt, user_message)
            responses.append(response)

        # Check pairwise similarity
        for i in range(len(responses)):
            for j in range(i + 1, len(responses)):
                sim = jaccard_similarity(responses[i], responses[j])
                if sim > 0.5:
                    import warnings
                    warnings.warn(
                        f"{personality_name}: Responses {i+1} and {j+1} too similar "
                        f"(Jaccard={sim:.2f}). May indicate templated output.\n"
                        f"Response {i+1}: {responses[i][:200]}\n"
                        f"Response {j+1}: {responses[j][:200]}",
                        stacklevel=1,
                    )
