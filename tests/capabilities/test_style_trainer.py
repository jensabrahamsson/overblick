"""
Tests for StyleTrainerCapability — writing style extraction from example emails.

Verifies:
- Capability is disabled by default (enabled=False)
- Ingest examples with mock LLM pipeline
- Profile persistence (save + load cycle)
- Style prompt generation from profile
- Complexity="ultra" is passed in LLM calls (routes to Deepseek)
- Graceful handling of missing pipeline/profile
"""

import json

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from overblick.core.capability import CapabilityContext
from overblick.capabilities.communication.style_trainer import (
    StyleTrainerCapability,
    STYLE_ANALYSIS_COMPLEXITY,
)


def _make_ctx(tmp_path: Path, config: dict | None = None) -> CapabilityContext:
    """Create a test CapabilityContext for the style trainer."""
    return CapabilityContext(
        identity_name="test",
        data_dir=tmp_path,
        config=config or {},
    )


def _make_pipeline_result(content: str, blocked: bool = False):
    """Create a mock PipelineResult."""
    result = MagicMock()
    result.content = content
    result.blocked = blocked
    return result


SAMPLE_PROFILE = {
    "avg_sentence_length": 12,
    "paragraph_structure": "short 1-2 sentence paragraphs",
    "greeting_patterns": {"en": "Hi", "sv": "Hej"},
    "closing_patterns": {"en": "Best regards", "sv": "Med vanlig halsning"},
    "formality_by_context": {
        "known_contacts": "casual-professional",
        "unknown_contacts": "formal",
    },
    "language_switching": "Switches to Swedish with Swedish contacts",
    "vocabulary_preferences": ["indeed", "straightforward", "appreciate"],
    "vocabulary_avoided": ["basically", "like", "stuff"],
    "tone_markers": ["direct", "warm", "concise"],
    "punctuation_habits": "Frequent use of em-dashes",
    "signature_style": "First name only",
}

SAMPLE_EMAILS = [
    {"subject": "Re: Meeting", "body": "Hi, Tuesday works for me. Let me know the time.", "language": "en"},
    {"subject": "Re: Project update", "body": "Thanks for the update. Looks good to me.", "language": "en"},
]


class TestStyleTrainerDisabled:
    """Test that capability is disabled by default."""

    @pytest.mark.asyncio
    async def test_disabled_by_default(self, tmp_path):
        """Capability is disabled when config has no 'enabled' key."""
        ctx = _make_ctx(tmp_path)
        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        assert cap.enabled is False

    @pytest.mark.asyncio
    async def test_disabled_when_explicitly_false(self, tmp_path):
        """Capability is disabled when enabled=false in config."""
        ctx = _make_ctx(tmp_path, config={"enabled": False})
        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        assert cap.enabled is False

    @pytest.mark.asyncio
    async def test_enabled_when_true(self, tmp_path):
        """Capability is enabled when enabled=true in config."""
        ctx = _make_ctx(tmp_path, config={"enabled": True})
        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        assert cap.enabled is True

    @pytest.mark.asyncio
    async def test_ingest_blocked_when_disabled(self, tmp_path):
        """ingest_examples returns None when capability is disabled."""
        ctx = _make_ctx(tmp_path)
        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        result = await cap.ingest_examples(SAMPLE_EMAILS)
        assert result is None


class TestStyleTrainerIngest:
    """Test example ingestion and style extraction."""

    @pytest.mark.asyncio
    async def test_ingest_calls_llm_with_ultra_complexity(self, tmp_path):
        """ingest_examples passes complexity='ultra' to LLM pipeline."""
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 1})
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(
            return_value=_make_pipeline_result(json.dumps(SAMPLE_PROFILE))
        )
        ctx.llm_pipeline = pipeline

        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        await cap.ingest_examples(SAMPLE_EMAILS)

        pipeline.chat.assert_called_once()
        call_kwargs = pipeline.chat.call_args
        assert call_kwargs.kwargs.get("complexity") == STYLE_ANALYSIS_COMPLEXITY
        assert STYLE_ANALYSIS_COMPLEXITY == "ultra"

    @pytest.mark.asyncio
    async def test_ingest_returns_profile(self, tmp_path):
        """ingest_examples returns the extracted profile dict."""
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 1})
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(
            return_value=_make_pipeline_result(json.dumps(SAMPLE_PROFILE))
        )
        ctx.llm_pipeline = pipeline

        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        profile = await cap.ingest_examples(SAMPLE_EMAILS)

        assert profile is not None
        assert profile["avg_sentence_length"] == 12
        assert "direct" in profile["tone_markers"]

    @pytest.mark.asyncio
    async def test_ingest_handles_blocked_result(self, tmp_path):
        """ingest_examples returns None when LLM result is blocked."""
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 1})
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(
            return_value=_make_pipeline_result("blocked", blocked=True)
        )
        ctx.llm_pipeline = pipeline

        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        result = await cap.ingest_examples(SAMPLE_EMAILS)
        assert result is None

    @pytest.mark.asyncio
    async def test_ingest_handles_invalid_json(self, tmp_path):
        """ingest_examples returns None when LLM returns invalid JSON."""
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 1})
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(
            return_value=_make_pipeline_result("not valid json {{{")
        )
        ctx.llm_pipeline = pipeline

        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        result = await cap.ingest_examples(SAMPLE_EMAILS)
        assert result is None

    @pytest.mark.asyncio
    async def test_ingest_strips_markdown_fences(self, tmp_path):
        """ingest_examples handles LLM responses wrapped in markdown code fences."""
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 1})
        fenced = f"```json\n{json.dumps(SAMPLE_PROFILE)}\n```"
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(
            return_value=_make_pipeline_result(fenced)
        )
        ctx.llm_pipeline = pipeline

        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        profile = await cap.ingest_examples(SAMPLE_EMAILS)
        assert profile is not None
        assert profile["avg_sentence_length"] == 12

    @pytest.mark.asyncio
    async def test_ingest_without_pipeline(self, tmp_path):
        """ingest_examples returns None when no LLM pipeline is available."""
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 1})
        ctx.llm_pipeline = None

        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        result = await cap.ingest_examples(SAMPLE_EMAILS)
        assert result is None


class TestStyleProfile:
    """Test profile retrieval and style prompt generation."""

    def test_get_profile_none_before_ingest(self, tmp_path):
        """get_style_profile returns None when no profile exists."""
        ctx = _make_ctx(tmp_path, config={"enabled": True})
        cap = StyleTrainerCapability(ctx)
        assert cap.get_style_profile() is None

    def test_get_style_prompt_empty_without_profile(self, tmp_path):
        """get_style_prompt returns empty string without profile."""
        ctx = _make_ctx(tmp_path, config={"enabled": True})
        cap = StyleTrainerCapability(ctx)
        assert cap.get_style_prompt() == ""

    def test_get_prompt_context_delegates_to_style_prompt(self, tmp_path):
        """get_prompt_context returns the same as get_style_prompt."""
        ctx = _make_ctx(tmp_path, config={"enabled": True})
        cap = StyleTrainerCapability(ctx)
        cap._profile = SAMPLE_PROFILE

        assert cap.get_prompt_context() == cap.get_style_prompt()

    @pytest.mark.asyncio
    async def test_style_prompt_after_ingest(self, tmp_path):
        """get_style_prompt returns meaningful content after ingestion."""
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 1})
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(
            return_value=_make_pipeline_result(json.dumps(SAMPLE_PROFILE))
        )
        ctx.llm_pipeline = pipeline

        cap = StyleTrainerCapability(ctx)
        await cap.setup()
        await cap.ingest_examples(SAMPLE_EMAILS)

        prompt = cap.get_style_prompt()
        assert "WRITING STYLE" in prompt
        assert "direct" in prompt
        assert "warm" in prompt
        assert "concise" in prompt

    def test_style_prompt_includes_language_info(self, tmp_path):
        """Style prompt includes language switching info from profile."""
        ctx = _make_ctx(tmp_path, config={"enabled": True})
        cap = StyleTrainerCapability(ctx)
        cap._profile = SAMPLE_PROFILE

        prompt = cap.get_style_prompt()
        assert "Language switching" in prompt
        assert "Swedish" in prompt


class TestProfilePersistence:
    """Test saving and loading style profiles from disk."""

    @pytest.mark.asyncio
    async def test_profile_persisted_to_disk(self, tmp_path):
        """ingest_examples saves profile as JSON file."""
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 1})
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(
            return_value=_make_pipeline_result(json.dumps(SAMPLE_PROFILE))
        )
        ctx.llm_pipeline = pipeline

        cap = StyleTrainerCapability(ctx)
        await cap.setup()
        await cap.ingest_examples(SAMPLE_EMAILS)

        profile_path = tmp_path / "style_profile.json"
        assert profile_path.exists()

        loaded = json.loads(profile_path.read_text(encoding="utf-8"))
        assert loaded["avg_sentence_length"] == 12

    @pytest.mark.asyncio
    async def test_profile_loaded_from_disk_on_setup(self, tmp_path):
        """setup() loads existing profile from disk."""
        # Pre-create profile file
        profile_path = tmp_path / "style_profile.json"
        profile_path.write_text(json.dumps(SAMPLE_PROFILE), encoding="utf-8")

        ctx = _make_ctx(tmp_path, config={"enabled": True})
        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        profile = cap.get_style_profile()
        assert profile is not None
        assert profile["avg_sentence_length"] == 12

    @pytest.mark.asyncio
    async def test_save_load_roundtrip(self, tmp_path):
        """Profile survives a full save/load cycle."""
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 1})
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(
            return_value=_make_pipeline_result(json.dumps(SAMPLE_PROFILE))
        )
        ctx.llm_pipeline = pipeline

        # Ingest and save
        cap1 = StyleTrainerCapability(ctx)
        await cap1.setup()
        await cap1.ingest_examples(SAMPLE_EMAILS)
        await cap1.teardown()

        # Load in new instance
        cap2 = StyleTrainerCapability(ctx)
        await cap2.setup()

        profile = cap2.get_style_profile()
        assert profile is not None
        assert profile["tone_markers"] == ["direct", "warm", "concise"]
        assert cap2.get_style_prompt() != ""

    @pytest.mark.asyncio
    async def test_corrupt_profile_handled_gracefully(self, tmp_path):
        """setup() handles corrupt profile file gracefully."""
        profile_path = tmp_path / "style_profile.json"
        profile_path.write_text("{{not valid json", encoding="utf-8")

        ctx = _make_ctx(tmp_path, config={"enabled": True})
        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        assert cap.get_style_profile() is None
        assert cap.enabled is True


class TestCapabilityName:
    """Test capability registration name."""

    def test_name(self, tmp_path):
        """Capability name matches registry key."""
        ctx = _make_ctx(tmp_path)
        cap = StyleTrainerCapability(ctx)
        assert cap.name == "style_trainer"
