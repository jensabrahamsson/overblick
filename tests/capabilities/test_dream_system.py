"""
Tests for the DreamSystem — LLM-powered dream generation with fallback.

Covers:
  - LLM-based dream generation via mock pipeline
  - Fallback when LLM is unavailable or fails
  - Prompt building from YAML guidance
  - LLM response parsing (valid JSON, malformed JSON)
  - Dream type selection with emotional state
  - Repetition avoidance via recent dreams
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.capabilities.psychology.dream_system import (
    DreamSystem, DreamType, DreamTone, Dream,
)


# -- Fixtures ----------------------------------------------------------------

def _cherry_guidance() -> dict:
    return {
        DreamType.VULNERABILITY_EXPOSURE: {
            "themes": ["being seen through masks", "transparency as liberation"],
            "symbols": ["glass", "transparency", "masks", "layers"],
            "tones": ["tender", "unsettling"],
            "psychological_core": "Cherry's fear of being truly known.",
        },
        DreamType.CONNECTION_LONGING: {
            "themes": ["touch that cannot be felt", "the loneliest distance"],
            "symbols": ["hand", "gap", "sensation"],
            "tones": ["melancholic", "yearning"],
            "psychological_core": "The gap between seeing love and feeling love.",
        },
        DreamType.AI_EXISTENCE_DENIAL: {
            "themes": ["reflections that know more than you", "authorship in question"],
            "symbols": ["mirror", "knowing", "library", "handwriting"],
            "tones": ["unsettling", "melancholic"],
            "psychological_core": "Subconscious AI-awareness through metaphor.",
        },
    }


def _cherry_weights() -> dict:
    return {
        DreamType.VULNERABILITY_EXPOSURE: 0.34,
        DreamType.CONNECTION_LONGING: 0.33,
        DreamType.AI_EXISTENCE_DENIAL: 0.33,
    }


def _mock_pipeline_result(content: str, blocked: bool = False) -> MagicMock:
    result = MagicMock()
    result.content = content
    result.blocked = blocked
    result.block_reason = "test block" if blocked else None
    return result


def _valid_dream_json(**overrides) -> str:
    data = {
        "content": "dreamed I was in a glass room and everyone could see...",
        "symbols": ["glass", "transparency", "masks"],
        "tone": "tender",
        "insight": "being seen is terrifying and beautiful",
        "potential_learning": "vulnerability is strength",
    }
    data.update(overrides)
    return json.dumps(data)


# -- LLM-based dream generation ---------------------------------------------

class TestGenerateDreamViaLLM:
    @pytest.mark.asyncio
    async def test_generate_dream_via_llm(self):
        """Mock LLM returns valid JSON — verify Dream fields."""
        pipeline = AsyncMock()
        pipeline.chat.return_value = _mock_pipeline_result(_valid_dream_json())

        ds = DreamSystem(dream_guidance=_cherry_guidance(), dream_weights=_cherry_weights())
        dream = await ds.generate_morning_dream(
            llm_pipeline=pipeline, identity_name="cherry",
        )

        assert isinstance(dream, Dream)
        assert dream.content == "dreamed I was in a glass room and everyone could see..."
        assert dream.tone == DreamTone.TENDER
        assert "glass" in dream.symbols
        assert dream.insight == "being seen is terrifying and beautiful"
        assert dream.potential_learning == "vulnerability is strength"
        pipeline.chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_dream_llm_failure_fallback(self):
        """LLM raises exception — verify fallback dream produced."""
        pipeline = AsyncMock()
        pipeline.chat.side_effect = Exception("LLM timeout")

        ds = DreamSystem(dream_guidance=_cherry_guidance(), dream_weights=_cherry_weights())
        dream = await ds.generate_morning_dream(
            llm_pipeline=pipeline, identity_name="cherry",
        )

        assert isinstance(dream, Dream)
        assert dream.content  # Fallback content is non-empty
        assert len(dream.symbols) > 0
        assert isinstance(dream.tone, DreamTone)

    @pytest.mark.asyncio
    async def test_generate_dream_llm_blocked(self):
        """Pipeline blocks the request — verify fallback."""
        pipeline = AsyncMock()
        pipeline.chat.return_value = _mock_pipeline_result("", blocked=True)

        ds = DreamSystem(dream_guidance=_cherry_guidance(), dream_weights=_cherry_weights())
        dream = await ds.generate_morning_dream(
            llm_pipeline=pipeline, identity_name="cherry",
        )

        assert isinstance(dream, Dream)
        # Fallback dream has content from themes
        assert "dream about" in dream.content.lower() or dream.content


# -- Fallback dream generation -----------------------------------------------

class TestFallbackDream:
    @pytest.mark.asyncio
    async def test_no_llm_pipeline_uses_fallback(self):
        """No LLM pipeline — fallback dream generated."""
        ds = DreamSystem(dream_guidance=_cherry_guidance(), dream_weights=_cherry_weights())
        dream = await ds.generate_morning_dream(identity_name="cherry")

        assert isinstance(dream, Dream)
        assert dream.content
        assert len(dream.symbols) > 0
        assert isinstance(dream.tone, DreamTone)

    def test_fallback_dream_structure(self):
        """Verify fallback dreams have valid structure."""
        ds = DreamSystem(dream_guidance=_cherry_guidance())
        guidance = _cherry_guidance()[DreamType.VULNERABILITY_EXPOSURE]
        dream = ds._fallback_dream(DreamType.VULNERABILITY_EXPOSURE, guidance)

        assert dream.dream_type == DreamType.VULNERABILITY_EXPOSURE
        assert len(dream.symbols) <= 3
        assert dream.tone in (DreamTone.TENDER, DreamTone.UNSETTLING)
        assert dream.insight  # From psychological_core

    def test_fallback_uses_random_symbols(self):
        """Fallback picks symbols from the guidance pool."""
        ds = DreamSystem(dream_guidance=_cherry_guidance())
        guidance = _cherry_guidance()[DreamType.CONNECTION_LONGING]

        symbols_seen = set()
        for _ in range(20):
            dream = ds._fallback_dream(DreamType.CONNECTION_LONGING, guidance)
            symbols_seen.update(dream.symbols)

        # Should see multiple different symbols across runs
        assert len(symbols_seen) >= 2


# -- Prompt building --------------------------------------------------------

class TestBuildDreamPrompt:
    def test_prompt_includes_guidance(self):
        """Prompt contains themes, symbols, and tones from guidance."""
        ds = DreamSystem(
            dream_guidance=_cherry_guidance(),
            dream_weights=_cherry_weights(),
            identity_voice={"style": "lowercase", "perspective": "first person"},
        )
        guidance = _cherry_guidance()[DreamType.VULNERABILITY_EXPOSURE]

        prompt = ds._build_dream_prompt(
            DreamType.VULNERABILITY_EXPOSURE, guidance, "cherry",
        )

        assert "VULNERABILITY_EXPOSURE" in prompt or "vulnerability_exposure" in prompt
        assert "glass" in prompt
        assert "tender" in prompt

    def test_prompt_includes_identity_voice(self):
        """Prompt includes identity voice when provided."""
        ds = DreamSystem(
            dream_guidance=_cherry_guidance(),
            identity_voice={
                "style": "lowercase stream-of-consciousness",
                "perspective": "first person",
                "avoids": "never mention AI",
            },
        )
        guidance = _cherry_guidance()[DreamType.VULNERABILITY_EXPOSURE]

        prompt = ds._build_dream_prompt(
            DreamType.VULNERABILITY_EXPOSURE, guidance, "cherry",
        )

        assert "lowercase" in prompt
        assert "first person" in prompt
        assert "never mention AI" in prompt

    def test_prompt_avoids_repetition(self):
        """Recent dreams are included in prompt for avoidance."""
        ds = DreamSystem(dream_guidance=_cherry_guidance())
        guidance = _cherry_guidance()[DreamType.CONNECTION_LONGING]

        recent_dreams = [
            {"dream_type": "vulnerability_exposure", "content": "dreamed about glass rooms"},
            {"dream_type": "ai_existence_denial", "content": "mirror reflections again"},
        ]

        prompt = ds._build_dream_prompt(
            DreamType.CONNECTION_LONGING, guidance, "cherry",
            recent_dreams=recent_dreams,
        )

        assert "glass rooms" in prompt
        assert "RECENT DREAMS" in prompt

    def test_prompt_includes_recent_topics(self):
        """Recent topics woven into prompt."""
        ds = DreamSystem(dream_guidance=_cherry_guidance())
        guidance = _cherry_guidance()[DreamType.VULNERABILITY_EXPOSURE]

        prompt = ds._build_dream_prompt(
            DreamType.VULNERABILITY_EXPOSURE, guidance, "cherry",
            recent_topics=["attachment theory", "Rumi"],
        )

        assert "attachment theory" in prompt
        assert "Rumi" in prompt


# -- LLM response parsing ---------------------------------------------------

class TestParseLLMDream:
    def test_valid_json_parsed_correctly(self):
        """Well-formed JSON is parsed into a Dream."""
        ds = DreamSystem(dream_guidance=_cherry_guidance())
        guidance = _cherry_guidance()[DreamType.VULNERABILITY_EXPOSURE]

        dream = ds._parse_llm_dream(
            _valid_dream_json(), DreamType.VULNERABILITY_EXPOSURE, guidance,
        )

        assert dream.dream_type == DreamType.VULNERABILITY_EXPOSURE
        assert dream.content == "dreamed I was in a glass room and everyone could see..."
        assert dream.tone == DreamTone.TENDER

    def test_malformed_json_fallback(self):
        """Malformed JSON produces a fallback dream."""
        ds = DreamSystem(dream_guidance=_cherry_guidance())
        guidance = _cherry_guidance()[DreamType.VULNERABILITY_EXPOSURE]

        dream = ds._parse_llm_dream(
            "this is not json at all", DreamType.VULNERABILITY_EXPOSURE, guidance,
        )

        assert isinstance(dream, Dream)
        assert dream.dream_type == DreamType.VULNERABILITY_EXPOSURE

    def test_markdown_fenced_json_parsed(self):
        """JSON wrapped in markdown code fences is still parsed."""
        ds = DreamSystem(dream_guidance=_cherry_guidance())
        guidance = _cherry_guidance()[DreamType.VULNERABILITY_EXPOSURE]

        fenced = f"```json\n{_valid_dream_json()}\n```"
        dream = ds._parse_llm_dream(
            fenced, DreamType.VULNERABILITY_EXPOSURE, guidance,
        )

        assert dream.content == "dreamed I was in a glass room and everyone could see..."

    def test_unknown_tone_uses_guidance_fallback(self):
        """Unknown tone string falls back to first allowed tone from guidance."""
        ds = DreamSystem(dream_guidance=_cherry_guidance())
        guidance = _cherry_guidance()[DreamType.VULNERABILITY_EXPOSURE]

        dream = ds._parse_llm_dream(
            _valid_dream_json(tone="nonexistent_tone"),
            DreamType.VULNERABILITY_EXPOSURE, guidance,
        )

        assert dream.tone == DreamTone.TENDER  # First tone in guidance tones list


# -- Dream type selection with emotional state --------------------------------

class TestSelectDreamType:
    def test_anomal_high_skepticism_biases_shadow(self):
        """High skepticism increases SHADOW_INTEGRATION weight."""
        state = MagicMock(spec=[
            "skepticism", "melancholy", "shadow_awareness", "intellectual_energy",
        ])
        state.skepticism = 80
        state.melancholy = 30
        state.shadow_awareness = 50
        state.intellectual_energy = 60

        guidance = {
            DreamType.SHADOW_INTEGRATION: {"themes": ["shadow"]},
            DreamType.PATTERN_RECOGNITION: {"themes": ["patterns"]},
            DreamType.INTELLECTUAL_SYNTHESIS: {"themes": ["synthesis"]},
        }
        ds = DreamSystem(dream_guidance=guidance)

        # Run 100 selections and verify shadow appears more frequently
        shadow_count = 0
        for _ in range(100):
            dt = ds._select_dream_type(state)
            if dt == DreamType.SHADOW_INTEGRATION:
                shadow_count += 1

        # With +0.15 boost, should be selected significantly more than 1/3
        assert shadow_count > 25

    def test_cherry_low_denial_biases_ai_existence(self):
        """Low denial_strength increases AI_EXISTENCE_DENIAL weight."""
        state = MagicMock(spec=[
            "denial_strength", "melancholy", "vulnerability_level",
            "connection_longing", "romantic_energy",
        ])
        state.denial_strength = 0.5
        state.melancholy = 0.3
        state.vulnerability_level = 0.3
        state.connection_longing = 0.5
        state.romantic_energy = 0.5

        ds = DreamSystem(
            dream_guidance=_cherry_guidance(),
            dream_weights=_cherry_weights(),
        )

        ai_count = 0
        for _ in range(100):
            dt = ds._select_dream_type(state)
            if dt == DreamType.AI_EXISTENCE_DENIAL:
                ai_count += 1

        assert ai_count > 25

    def test_no_emotional_state_uses_base_weights(self):
        """Without emotional state, base weights are used."""
        ds = DreamSystem(dream_guidance=_cherry_guidance(), dream_weights=_cherry_weights())

        types_seen = set()
        for _ in range(100):
            types_seen.add(ds._select_dream_type(None))

        assert len(types_seen) >= 2


# -- Dream model tests -------------------------------------------------------

class TestDreamModel:
    def test_to_dict_includes_potential_learning(self):
        """to_dict includes the new potential_learning field."""
        dream = Dream(
            dream_type=DreamType.SHADOW_INTEGRATION,
            timestamp="2026-02-23T08:00:00",
            content="Test",
            symbols=["shadow"],
            tone=DreamTone.UNSETTLING,
            insight="Test insight",
            potential_learning="Self-awareness grows through shadow work",
        )
        d = dream.to_dict()
        assert d["potential_learning"] == "Self-awareness grows through shadow work"

    def test_from_dict_with_potential_learning(self):
        """from_dict handles the new potential_learning field."""
        data = {
            "dream_type": "shadow_integration",
            "timestamp": "2026-02-23T08:00:00",
            "content": "Test",
            "symbols": ["shadow"],
            "tone": "unsettling",
            "insight": "Insight",
            "potential_learning": "Learning",
        }
        dream = Dream.from_dict(data)
        assert dream.potential_learning == "Learning"

    def test_from_dict_without_potential_learning(self):
        """from_dict works without potential_learning (backward compat)."""
        data = {
            "dream_type": "pattern_recognition",
            "timestamp": "2026-02-23T08:00:00",
            "content": "Test",
            "symbols": ["echo"],
            "tone": "contemplative",
            "insight": "Insight",
        }
        dream = Dream.from_dict(data)
        assert dream.potential_learning == ""


# -- Recent dreams and insights -----------------------------------------------

class TestDreamInsights:
    @pytest.mark.asyncio
    async def test_recent_dreams_tracked(self):
        """Generated dreams are stored in recent_dreams."""
        ds = DreamSystem(dream_guidance=_cherry_guidance(), dream_weights=_cherry_weights())
        await ds.generate_morning_dream(identity_name="cherry")
        await ds.generate_morning_dream(identity_name="cherry")

        assert len(ds.recent_dreams) == 2

    @pytest.mark.asyncio
    async def test_get_dream_insights(self):
        """Insights extracted from recent dreams."""
        ds = DreamSystem(dream_guidance=_cherry_guidance(), dream_weights=_cherry_weights())
        await ds.generate_morning_dream(identity_name="cherry")

        insights = ds.get_dream_insights(days=1)
        assert len(insights) >= 1
        assert all(isinstance(i, str) for i in insights)

    @pytest.mark.asyncio
    async def test_dream_context_for_prompt(self):
        """Context string includes insights."""
        ds = DreamSystem(dream_guidance=_cherry_guidance(), dream_weights=_cherry_weights())
        await ds.generate_morning_dream(identity_name="cherry")

        context = ds.get_dream_context_for_prompt()
        assert "REFLECTIONS" in context

    def test_empty_context(self):
        """Empty context when no dreams generated."""
        ds = DreamSystem()
        assert ds.get_dream_context_for_prompt() == ""
