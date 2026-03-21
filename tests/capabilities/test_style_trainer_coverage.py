"""
Additional coverage tests for style_trainer module.

Covers uncovered lines:
- ingest_examples: too few examples
- get_style_prompt: profile with no optional fields
- _save_profile: OSError during write
- _format_patterns: with empty dict
- teardown: with no profile
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.capabilities.communication.style_trainer import (
    StyleTrainerCapability,
    _format_patterns,
)
from overblick.core.capability import CapabilityContext


def _make_ctx(tmp_path, config=None):
    return CapabilityContext(
        identity_name="test",
        data_dir=tmp_path,
        config=config or {},
    )


class TestIngestTooFewExamples:
    @pytest.mark.asyncio
    async def test_should_reject_insufficient_examples(self, tmp_path):
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 10})
        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        result = await cap.ingest_examples([{"body": "short"}])
        assert result is None


class TestIngestEmailFields:
    @pytest.mark.asyncio
    async def test_should_include_all_email_fields_in_prompt(self, tmp_path):
        """Covers the 'language' branch in ingest_examples."""
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 1})
        profile = {"avg_sentence_length": 12, "tone_markers": ["warm"]}
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=MagicMock(
            content=json.dumps(profile), blocked=False
        ))
        ctx.llm_pipeline = pipeline

        cap = StyleTrainerCapability(ctx)
        await cap.setup()

        emails = [
            {"subject": "Re: Test", "to": "alice@example.com", "language": "sv", "body": "Hej"},
        ]
        result = await cap.ingest_examples(emails)
        assert result is not None

        # Verify the prompt included all fields
        call_args = pipeline.chat.call_args
        prompt_messages = call_args[1]["messages"]
        prompt_text = prompt_messages[0]["content"]
        assert "Language: sv" in prompt_text
        assert "Subject:" in prompt_text
        assert "To:" in prompt_text


class TestGetStylePromptMinimalProfile:
    def test_should_handle_profile_with_no_optional_fields(self, tmp_path):
        ctx = _make_ctx(tmp_path, config={"enabled": True})
        cap = StyleTrainerCapability(ctx)
        cap._profile = {
            "tone_markers": ["direct"],
            "avg_sentence_length": 10,
        }

        prompt = cap.get_style_prompt()
        assert "direct" in prompt
        assert "none specified" in prompt  # vocabulary/avoided fallback


class TestSaveProfileError:
    def test_should_handle_os_error_on_save(self, tmp_path):
        ctx = _make_ctx(tmp_path, config={"enabled": True})
        cap = StyleTrainerCapability(ctx)
        cap._profile = {"test": True}
        cap._profile_path = tmp_path / "profile.json"

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            cap._save_profile()  # Should not raise


class TestTeardownNoProfile:
    @pytest.mark.asyncio
    async def test_should_noop_when_no_profile(self, tmp_path):
        ctx = _make_ctx(tmp_path, config={"enabled": True})
        cap = StyleTrainerCapability(ctx)
        await cap.setup()
        cap._profile = None
        await cap.teardown()  # Should not raise


class TestFormatPatterns:
    def test_should_return_default_for_empty_dict(self):
        assert _format_patterns({}) == "default"

    def test_should_format_lang_patterns(self):
        result = _format_patterns({"en": "Hi", "sv": "Hej"})
        assert 'en: "Hi"' in result
        assert 'sv: "Hej"' in result


class TestIngestMarkdownFencesNonClosing:
    @pytest.mark.asyncio
    async def test_should_handle_markdown_fences_without_closing(self, tmp_path):
        """Covers the else branch in markdown fence stripping."""
        ctx = _make_ctx(tmp_path, config={"enabled": True, "min_examples": 1})
        profile = {"avg_sentence_length": 12, "tone_markers": ["warm"]}
        # Fence that does NOT end with ``` — last line is not just "```"
        fenced = f"```json\n{json.dumps(profile)}"
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(return_value=MagicMock(content=fenced, blocked=False))
        ctx.llm_pipeline = pipeline

        cap = StyleTrainerCapability(ctx)
        await cap.setup()
        result = await cap.ingest_examples([{"body": "x"}])
        assert result is not None
