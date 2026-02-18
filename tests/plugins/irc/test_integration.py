"""Integration tests for IRC plugin — _generate_turn() and _run_turns() methods.

These test the core conversation engine: LLM calls, turn management,
event emission, and persistence.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.core.llm.pipeline import PipelineResult
from overblick.plugins.irc.models import ConversationState, IRCConversation, IRCTurn


# ---------------------------------------------------------------------------
# TestGenerateTurn — tests for _generate_turn()
# ---------------------------------------------------------------------------


class TestGenerateTurn:
    """Tests for IRCPlugin._generate_turn() — single turn generation via LLM."""

    def _setup_plugin(self, irc_plugin, mock_ctx, participants=None):
        """Helper: set up plugin with a conversation and mock identities."""
        if participants is None:
            participants = ["anomal", "cherry"]

        irc_plugin._data_dir = mock_ctx.data_dir / "irc"
        irc_plugin._data_dir.mkdir(parents=True, exist_ok=True)

        for name in participants:
            identity = MagicMock()
            identity.name = name
            identity.display_name = name.capitalize()
            identity.interest_keywords = ["test"]
            irc_plugin._identities[name] = identity

        conv = IRCConversation(
            id="irc-test-gen",
            topic="Test Topic",
            topic_description="A topic for testing.",
            participants=participants,
            max_turns=10,
        )
        irc_plugin._current_conversation = conv

    @pytest.mark.asyncio
    async def test_generate_turn_returns_content(self, irc_plugin, mock_ctx):
        """Basic case: generate a turn and get content back."""
        self._setup_plugin(irc_plugin, mock_ctx)

        with patch("overblick.identities.build_system_prompt", return_value="System prompt"):
            result = await irc_plugin._generate_turn("anomal")

        assert result == "This is a test response from the IRC conversation."

    @pytest.mark.asyncio
    async def test_generate_turn_builds_system_prompt(self, irc_plugin, mock_ctx):
        """Verify that messages passed to chat() include IRC context."""
        self._setup_plugin(irc_plugin, mock_ctx)

        with patch("overblick.identities.build_system_prompt", return_value="Base prompt"):
            await irc_plugin._generate_turn("anomal")

        call_kwargs = mock_ctx.llm_pipeline.chat.call_args.kwargs
        messages = call_kwargs["messages"]
        system_msg = messages[0]["content"]

        assert messages[0]["role"] == "system"
        assert "Base prompt" in system_msg
        assert "IRC CONVERSATION CONTEXT" in system_msg
        assert "Test Topic" in system_msg

    @pytest.mark.asyncio
    async def test_generate_turn_includes_conversation_history(self, irc_plugin, mock_ctx):
        """Earlier turns should appear as user/assistant messages."""
        self._setup_plugin(irc_plugin, mock_ctx)

        # Add existing turns to the conversation
        turns = [
            IRCTurn(identity="cherry", display_name="Cherry", content="Opening thought.", turn_number=0),
            IRCTurn(identity="anomal", display_name="Anomal", content="My reply.", turn_number=1),
        ]
        irc_plugin._current_conversation = irc_plugin._current_conversation.model_copy(
            update={"turns": turns}
        )

        with patch("overblick.identities.build_system_prompt", return_value="Prompt"):
            await irc_plugin._generate_turn("anomal")

        call_kwargs = mock_ctx.llm_pipeline.chat.call_args.kwargs
        messages = call_kwargs["messages"]

        # system + 2 history turns + 1 continuation prompt = 4 messages
        assert len(messages) == 4
        # Cherry's turn should be "user" (from anomal's perspective)
        assert messages[1]["role"] == "user"
        assert "[Cherry]" in messages[1]["content"]
        # Anomal's own turn should be "assistant"
        assert messages[2]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_generate_turn_blocked_returns_none(self, irc_plugin, mock_ctx):
        """When pipeline blocks the response, return None."""
        self._setup_plugin(irc_plugin, mock_ctx)
        mock_ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(blocked=True, block_reason="Safety filter")
        )

        with patch("overblick.identities.build_system_prompt", return_value="Prompt"):
            result = await irc_plugin._generate_turn("anomal")

        assert result is None

    @pytest.mark.asyncio
    async def test_generate_turn_no_pipeline_returns_none(self, irc_plugin, mock_ctx):
        """When no LLM pipeline is available, return None."""
        self._setup_plugin(irc_plugin, mock_ctx)
        mock_ctx.llm_pipeline = None

        with patch("overblick.identities.build_system_prompt", return_value="Prompt"):
            result = await irc_plugin._generate_turn("anomal")

        assert result is None

    @pytest.mark.asyncio
    async def test_generate_turn_unknown_identity_returns_none(self, irc_plugin, mock_ctx):
        """Unknown identity name should return None immediately."""
        self._setup_plugin(irc_plugin, mock_ctx)

        result = await irc_plugin._generate_turn("nonexistent")

        assert result is None
        # chat() should not have been called
        mock_ctx.llm_pipeline.chat.assert_not_called()


# ---------------------------------------------------------------------------
# TestRunTurns — tests for _run_turns()
# ---------------------------------------------------------------------------


class TestRunTurns:
    """Tests for IRCPlugin._run_turns() — multi-turn conversation loop."""

    def _setup_plugin(self, irc_plugin, mock_ctx, participants=None, max_turns=10):
        """Helper: set up plugin with conversation and identities."""
        if participants is None:
            participants = ["anomal", "cherry"]

        irc_plugin._data_dir = mock_ctx.data_dir / "irc"
        irc_plugin._data_dir.mkdir(parents=True, exist_ok=True)

        for name in participants:
            identity = MagicMock()
            identity.name = name
            identity.display_name = name.capitalize()
            identity.interest_keywords = ["test"]
            irc_plugin._identities[name] = identity

        conv = IRCConversation(
            id="irc-test-run",
            topic="Run Test",
            topic_description="Testing run_turns.",
            participants=participants,
            max_turns=max_turns,
        )
        irc_plugin._current_conversation = conv

    @pytest.mark.asyncio
    async def test_run_turns_adds_turns(self, irc_plugin, mock_ctx):
        """Turns should be added to the conversation."""
        self._setup_plugin(irc_plugin, mock_ctx)

        with patch("overblick.identities.build_system_prompt", return_value="Prompt"), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await irc_plugin._run_turns(max_turns=2)

        assert irc_plugin._current_conversation.turn_count == 2

    @pytest.mark.asyncio
    async def test_run_turns_saves_after_each_turn(self, irc_plugin, mock_ctx):
        """Conversation should be saved to disk after each turn."""
        self._setup_plugin(irc_plugin, mock_ctx)

        with patch("overblick.identities.build_system_prompt", return_value="Prompt"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch.object(irc_plugin, "_save_conversation") as mock_save:
            await irc_plugin._run_turns(max_turns=3)

        assert mock_save.call_count == 3

    @pytest.mark.asyncio
    async def test_run_turns_emits_events(self, irc_plugin, mock_ctx):
        """irc.new_turn event should be emitted for each turn."""
        self._setup_plugin(irc_plugin, mock_ctx)

        with patch("overblick.identities.build_system_prompt", return_value="Prompt"), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await irc_plugin._run_turns(max_turns=2)

        # Filter for irc.new_turn calls
        new_turn_calls = [
            call for call in mock_ctx.event_bus.emit.call_args_list
            if call.args[0] == "irc.new_turn"
        ]
        assert len(new_turn_calls) == 2

    @pytest.mark.asyncio
    async def test_run_turns_respects_max_turns(self, irc_plugin, mock_ctx):
        """Should not exceed the requested max_turns."""
        self._setup_plugin(irc_plugin, mock_ctx)

        with patch("overblick.identities.build_system_prompt", return_value="Prompt"), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await irc_plugin._run_turns(max_turns=1)

        assert irc_plugin._current_conversation.turn_count == 1

    @pytest.mark.asyncio
    async def test_run_turns_ends_conversation_at_limit(self, irc_plugin, mock_ctx):
        """When conversation reaches max_turns, state should become COMPLETED."""
        self._setup_plugin(irc_plugin, mock_ctx, max_turns=2)

        with patch("overblick.identities.build_system_prompt", return_value="Prompt"), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            # Run enough turns to hit max_turns
            await irc_plugin._run_turns(max_turns=3)

        assert irc_plugin._current_conversation.state == ConversationState.COMPLETED

    @pytest.mark.asyncio
    async def test_run_turns_stops_on_generate_failure(self, irc_plugin, mock_ctx):
        """When _generate_turn returns None, the loop should stop."""
        self._setup_plugin(irc_plugin, mock_ctx)
        mock_ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(blocked=True, block_reason="Blocked")
        )

        with patch("overblick.identities.build_system_prompt", return_value="Prompt"), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await irc_plugin._run_turns(max_turns=3)

        assert irc_plugin._current_conversation.turn_count == 0


# ---------------------------------------------------------------------------
# TestConversationFlow — full end-to-end flow tests
# ---------------------------------------------------------------------------


class TestConversationFlow:
    """Full conversation lifecycle tests: tick -> turns -> persist."""

    @pytest.mark.asyncio
    async def test_full_conversation_tick_to_turns(self, irc_plugin, mock_ctx):
        """tick() should start a conversation and run turns."""
        irc_plugin._running = True
        irc_plugin._data_dir = mock_ctx.data_dir / "irc"
        irc_plugin._data_dir.mkdir(parents=True, exist_ok=True)

        for name in ["anomal", "cherry"]:
            identity = MagicMock()
            identity.name = name
            identity.display_name = name.capitalize()
            identity.interest_keywords = ["AI", "philosophy"]
            identity.interests = {}
            irc_plugin._identities[name] = identity

        with patch.object(irc_plugin, "_is_irc_quiet_hours", return_value=False), \
             patch.object(irc_plugin, "_is_system_idle", new_callable=AsyncMock, return_value=True), \
             patch("overblick.identities.build_system_prompt", return_value="Prompt"), \
             patch("asyncio.sleep", new_callable=AsyncMock), \
             patch("overblick.plugins.irc.plugin.select_topic", return_value={
                 "id": "test-topic",
                 "topic": "Test Topic",
                 "description": "A test.",
                 "tags": ["AI"],
             }), \
             patch("overblick.plugins.irc.plugin.select_participants") as mock_parts:
            # select_participants returns identity objects
            mock_parts.return_value = list(irc_plugin._identities.values())
            await irc_plugin.tick()

        conv = irc_plugin._current_conversation
        assert conv is not None
        assert conv.turn_count > 0

    @pytest.mark.asyncio
    async def test_conversation_persistence_cycle(self, irc_plugin, mock_ctx):
        """Save a conversation with turns, reload, verify turns intact."""
        irc_plugin._data_dir = mock_ctx.data_dir / "irc"
        irc_plugin._data_dir.mkdir(parents=True, exist_ok=True)

        conv = IRCConversation(
            id="irc-persist",
            topic="Persistence",
            participants=["anomal", "cherry"],
            turns=[
                IRCTurn(identity="anomal", display_name="Anomal", content="First turn.", turn_number=0),
                IRCTurn(identity="cherry", display_name="Cherry", content="Second turn.", turn_number=1),
                IRCTurn(identity="anomal", display_name="Anomal", content="Third turn.", turn_number=2),
            ],
            state=ConversationState.COMPLETED,
        )
        irc_plugin._save_conversation(conv)

        loaded = irc_plugin._load_conversations()
        assert len(loaded) == 1
        assert loaded[0].turn_count == 3
        assert loaded[0].turns[0].content == "First turn."
        assert loaded[0].turns[2].content == "Third turn."
        assert loaded[0].state == ConversationState.COMPLETED

    @pytest.mark.asyncio
    async def test_conversation_cancelled_on_teardown(self, irc_plugin, mock_ctx):
        """Active conversation should be cancelled on teardown."""
        irc_plugin._data_dir = mock_ctx.data_dir / "irc"
        irc_plugin._data_dir.mkdir(parents=True, exist_ok=True)
        irc_plugin._running = True

        for name in ["anomal", "cherry"]:
            identity = MagicMock()
            identity.name = name
            identity.display_name = name.capitalize()
            irc_plugin._identities[name] = identity

        # Set up an active conversation with turns
        conv = IRCConversation(
            id="irc-teardown",
            topic="Teardown Test",
            participants=["anomal", "cherry"],
            turns=[
                IRCTurn(identity="anomal", display_name="Anomal", content="Active turn.", turn_number=0),
            ],
        )
        irc_plugin._current_conversation = conv

        await irc_plugin.teardown()

        assert irc_plugin._current_conversation.state == ConversationState.CANCELLED
        assert irc_plugin._running is False
