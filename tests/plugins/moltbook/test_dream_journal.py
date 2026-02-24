"""
Tests for dream journal posting in MoltbookPlugin.

Verifies:
  - _maybe_post_dream_journal() posts a dream journal when conditions are met
  - Dream journal is only posted once per day
  - No post when no dream exists
  - No post when no DREAM_JOURNAL_PROMPT exists
  - ResponseGenerator.generate_dream_post() passes all dream fields
  - Integration with DreamCapability.last_dream
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.capabilities.engagement.response_gen import ResponseGenerator
from overblick.capabilities.psychology.dream_system import Dream, DreamTone, DreamType
from overblick.plugins.moltbook.models import Post


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dream(**overrides) -> Dream:
    """Create a test Dream instance."""
    defaults = dict(
        dream_type=DreamType.SHADOW_INTEGRATION,
        timestamp="2026-02-24T07:00:00",
        content="I walked through a hall of mirrors and each reflection showed a different face.",
        symbols=["mirror", "shadow", "face"],
        tone=DreamTone.UNSETTLING,
        insight="The masks we wear hide even from ourselves.",
        potential_learning="Authenticity requires facing the shadow.",
    )
    defaults.update(overrides)
    return Dream(**defaults)


class _MockPromptsWithDreamJournal:
    """Prompts module with DREAM_JOURNAL_PROMPT."""
    SYSTEM_PROMPT = "You are a test agent."
    COMMENT_PROMPT = "Respond to: {title}\n{content}"
    REPLY_PROMPT = "Reply to: {comment}\nOn post: {title}"
    HEARTBEAT_PROMPT = "Write about topic {topic_index}."
    SUBMOLT_INSTRUCTION = "Choose a submolt."
    DREAM_JOURNAL_PROMPT = (
        "Write about this dream:\n"
        "Type: {dream_type}\n"
        "Tone: {dream_tone}\n"
        "Content: {dream_content}\n"
        "Insight: {dream_insight}\n"
        "Symbols: {dream_symbols}\n"
        "{submolt_instruction}"
    )


class _MockPromptsNoDreamJournal:
    """Prompts module WITHOUT DREAM_JOURNAL_PROMPT."""
    SYSTEM_PROMPT = "You are a test agent."
    COMMENT_PROMPT = "Respond to: {title}\n{content}"


# ---------------------------------------------------------------------------
# ResponseGenerator.generate_dream_post()
# ---------------------------------------------------------------------------

class TestGenerateDreamPost:
    @pytest.mark.asyncio
    async def test_formats_all_dream_fields(self):
        """All dream fields are passed to the prompt template."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=MagicMock(
            blocked=False,
            content="SUBMOLT: philosophy\nTITLE: Morning Fragments: Mirrors\n\nThe dream content...",
        ))

        gen = ResponseGenerator(llm_pipeline=mock_pipeline, system_prompt="Test")
        dream = _make_dream()
        result = await gen.generate_dream_post(
            dream=dream.to_dict(),
            prompt_template=_MockPromptsWithDreamJournal.DREAM_JOURNAL_PROMPT,
            extra_format_vars={"submolt_instruction": "Pick a submolt."},
        )

        assert result is not None
        title, content, submolt = result
        assert title == "Morning Fragments: Mirrors"
        assert submolt == "philosophy"

        # Verify the prompt included dream data
        call_args = mock_pipeline.chat.call_args
        user_prompt = call_args.kwargs["messages"][1]["content"]
        assert "shadow_integration" in user_prompt
        assert "mirror" in user_prompt
        assert "unsettling" in user_prompt
        assert "masks we wear" in user_prompt

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self):
        """LLM returning None produces no result."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=MagicMock(
            blocked=False, content=None,
        ))

        gen = ResponseGenerator(llm_pipeline=mock_pipeline, system_prompt="Test")
        result = await gen.generate_dream_post(
            dream=_make_dream().to_dict(),
            prompt_template="{dream_content}",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_blocked_returns_none(self):
        """Pipeline blocking the request returns None."""
        mock_pipeline = AsyncMock()
        mock_pipeline.chat = AsyncMock(return_value=MagicMock(
            blocked=True, block_reason="test", block_stage=MagicMock(value="output_safety"),
        ))

        gen = ResponseGenerator(llm_pipeline=mock_pipeline, system_prompt="Test")
        result = await gen.generate_dream_post(
            dream=_make_dream().to_dict(),
            prompt_template="{dream_content}",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_template_key_returns_none(self):
        """Template with unknown placeholder returns None gracefully."""
        mock_pipeline = AsyncMock()
        gen = ResponseGenerator(llm_pipeline=mock_pipeline, system_prompt="Test")
        result = await gen.generate_dream_post(
            dream=_make_dream().to_dict(),
            prompt_template="{nonexistent_key}",
        )
        assert result is None


# ---------------------------------------------------------------------------
# MoltbookPlugin._maybe_post_dream_journal()
# ---------------------------------------------------------------------------

class TestMaybePostDreamJournal:
    """Tests for the dream journal posting mechanism in MoltbookPlugin."""

    def _make_plugin(self, mock_moltbook_client, mock_response_gen, dream_cap=None, prompts_cls=None):
        """Create a minimal MoltbookPlugin with mocks wired in."""
        from overblick.plugins.moltbook.plugin import MoltbookPlugin

        plugin = MoltbookPlugin.__new__(MoltbookPlugin)
        plugin._client = mock_moltbook_client
        plugin._response_gen = mock_response_gen
        plugin._dream_system = dream_cap
        plugin._dream_journal_posted_date = None

        # Minimal ctx mock
        plugin.ctx = MagicMock()
        plugin.ctx.identity.name = "anomal"
        plugin.ctx.engagement_db = AsyncMock()
        plugin.ctx.engagement_db.track_my_post = AsyncMock()
        plugin.ctx.audit_log = MagicMock()

        # Prompts
        if prompts_cls is None:
            prompts_cls = _MockPromptsWithDreamJournal
        plugin._load_prompts = MagicMock(return_value=prompts_cls())

        return plugin

    @pytest.mark.asyncio
    async def test_posts_dream_journal(self):
        """Happy path: dream exists, prompt exists, LLM succeeds — journal is posted."""
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(return_value=Post(
            id="post-dream-001", agent_id="agent-001", agent_name="Anomal",
            title="Morning Fragments: Mirrors", content="Dream content",
        ))

        mock_gen = AsyncMock()
        mock_gen.generate_dream_post = AsyncMock(return_value=(
            "Morning Fragments: Mirrors",
            "The dream was haunting and beautiful...",
            "philosophy",
        ))

        dream_cap = MagicMock()
        dream_cap.last_dream = _make_dream()

        plugin = self._make_plugin(mock_client, mock_gen, dream_cap)
        result = await plugin._maybe_post_dream_journal()

        assert result is True
        mock_gen.generate_dream_post.assert_awaited_once()
        mock_client.create_post.assert_awaited_once_with(
            "Morning Fragments: Mirrors",
            "The dream was haunting and beautiful...",
            submolt="philosophy",
        )
        plugin.ctx.engagement_db.track_my_post.assert_awaited_once()
        plugin.ctx.audit_log.log.assert_called_once()
        assert plugin._dream_journal_posted_date == date.today()

    @pytest.mark.asyncio
    async def test_no_dream_system(self):
        """No dream capability — returns False immediately."""
        plugin = self._make_plugin(AsyncMock(), AsyncMock(), dream_cap=None)
        result = await plugin._maybe_post_dream_journal()
        assert result is False

    @pytest.mark.asyncio
    async def test_no_dream_generated(self):
        """Dream capability exists but no dream generated yet."""
        dream_cap = MagicMock()
        dream_cap.last_dream = None

        plugin = self._make_plugin(AsyncMock(), AsyncMock(), dream_cap)
        result = await plugin._maybe_post_dream_journal()
        assert result is False

    @pytest.mark.asyncio
    async def test_already_posted_today(self):
        """Dream journal already posted today — skips."""
        dream_cap = MagicMock()
        dream_cap.last_dream = _make_dream()

        mock_gen = AsyncMock()
        plugin = self._make_plugin(AsyncMock(), mock_gen, dream_cap)
        plugin._dream_journal_posted_date = date.today()

        result = await plugin._maybe_post_dream_journal()
        assert result is False
        mock_gen.generate_dream_post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_dream_journal_prompt(self):
        """Identity has no DREAM_JOURNAL_PROMPT — skips."""
        dream_cap = MagicMock()
        dream_cap.last_dream = _make_dream()

        mock_gen = AsyncMock()
        plugin = self._make_plugin(
            AsyncMock(), mock_gen, dream_cap,
            prompts_cls=_MockPromptsNoDreamJournal,
        )

        result = await plugin._maybe_post_dream_journal()
        assert result is False
        mock_gen.generate_dream_post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_llm_generation_fails(self):
        """LLM returns None — no post created."""
        dream_cap = MagicMock()
        dream_cap.last_dream = _make_dream()

        mock_gen = AsyncMock()
        mock_gen.generate_dream_post = AsyncMock(return_value=None)

        plugin = self._make_plugin(AsyncMock(), mock_gen, dream_cap)
        result = await plugin._maybe_post_dream_journal()
        assert result is False
        assert plugin._dream_journal_posted_date is None

    @pytest.mark.asyncio
    async def test_create_post_fails(self):
        """create_post returns None — journal not marked as posted."""
        dream_cap = MagicMock()
        dream_cap.last_dream = _make_dream()

        mock_gen = AsyncMock()
        mock_gen.generate_dream_post = AsyncMock(return_value=(
            "Title", "Content", "philosophy",
        ))

        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(return_value=None)

        plugin = self._make_plugin(mock_client, mock_gen, dream_cap)
        result = await plugin._maybe_post_dream_journal()
        assert result is False
        assert plugin._dream_journal_posted_date is None

    @pytest.mark.asyncio
    async def test_rate_limit_error(self):
        """RateLimitError during posting — returns False, does not raise."""
        from overblick.plugins.moltbook.client import RateLimitError

        dream_cap = MagicMock()
        dream_cap.last_dream = _make_dream()

        mock_gen = AsyncMock()
        mock_gen.generate_dream_post = AsyncMock(return_value=(
            "Title", "Content", "philosophy",
        ))

        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(side_effect=RateLimitError("rate limited"))

        plugin = self._make_plugin(mock_client, mock_gen, dream_cap)
        result = await plugin._maybe_post_dream_journal()
        assert result is False

    @pytest.mark.asyncio
    async def test_suspension_error_propagates(self):
        """SuspensionError during posting — re-raised for tick() to handle."""
        from overblick.plugins.moltbook.client import SuspensionError

        dream_cap = MagicMock()
        dream_cap.last_dream = _make_dream()

        mock_gen = AsyncMock()
        mock_gen.generate_dream_post = AsyncMock(return_value=(
            "Title", "Content", "philosophy",
        ))

        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(side_effect=SuspensionError("suspended"))

        plugin = self._make_plugin(mock_client, mock_gen, dream_cap)
        with pytest.raises(SuspensionError):
            await plugin._maybe_post_dream_journal()

    @pytest.mark.asyncio
    async def test_dream_dict_passed_to_generator(self):
        """The full dream dict is passed to generate_dream_post."""
        dream_cap = MagicMock()
        dream_cap.last_dream = _make_dream()

        mock_gen = AsyncMock()
        mock_gen.generate_dream_post = AsyncMock(return_value=None)

        plugin = self._make_plugin(AsyncMock(), mock_gen, dream_cap)
        await plugin._maybe_post_dream_journal()

        call_kwargs = mock_gen.generate_dream_post.call_args.kwargs
        dream_dict = call_kwargs["dream"]
        assert dream_dict["dream_type"] == "shadow_integration"
        assert dream_dict["content"] == "I walked through a hall of mirrors and each reflection showed a different face."
        assert "mirror" in dream_dict["symbols"]
        assert call_kwargs["extra_format_vars"]["submolt_instruction"] == "Choose a submolt."

    @pytest.mark.asyncio
    async def test_submolt_instruction_passed(self):
        """SUBMOLT_INSTRUCTION from prompts module is passed as format var."""
        dream_cap = MagicMock()
        dream_cap.last_dream = _make_dream()

        mock_gen = AsyncMock()
        mock_gen.generate_dream_post = AsyncMock(return_value=None)

        plugin = self._make_plugin(AsyncMock(), mock_gen, dream_cap)
        await plugin._maybe_post_dream_journal()

        call_kwargs = mock_gen.generate_dream_post.call_args.kwargs
        assert "submolt_instruction" in call_kwargs["extra_format_vars"]


# ---------------------------------------------------------------------------
# DreamCapability.last_dream integration
# ---------------------------------------------------------------------------

class TestDreamCapabilityLastDream:
    @pytest.mark.asyncio
    async def test_last_dream_set_after_tick(self):
        """DreamCapability.last_dream is set after tick generates a dream."""
        from overblick.capabilities.psychology.dream import DreamCapability, _load_dream_guidance
        from overblick.core.capability import CapabilityContext

        ctx = MagicMock(spec=CapabilityContext)
        ctx.identity_name = "anomal"
        ctx.engagement_db = None
        ctx.llm_pipeline = None

        cap = DreamCapability(ctx)
        # Set up inner dream system directly
        from overblick.capabilities.psychology.dream_system import DreamSystem
        cap._dream_system = DreamSystem()

        assert cap.last_dream is None

        # Simulate tick
        cap._last_dream_date = None
        with patch("overblick.capabilities.psychology.dream.datetime") as mock_dt:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            mock_now = datetime(2026, 2, 24, 8, 0, tzinfo=ZoneInfo("Europe/Stockholm"))
            mock_dt.now.return_value = mock_now

            await cap.tick()

        assert cap.last_dream is not None
        assert cap.last_dream.content  # Has content

    def test_last_dream_none_initially(self):
        """last_dream is None before any dream is generated."""
        from overblick.capabilities.psychology.dream import DreamCapability
        ctx = MagicMock()
        ctx.identity_name = "test"
        cap = DreamCapability(ctx)
        assert cap.last_dream is None
