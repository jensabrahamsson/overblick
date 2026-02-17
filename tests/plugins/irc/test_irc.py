"""Tests for the IRC plugin — conversation orchestration, topic management, models."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.plugins.irc.models import (
    ConversationState,
    IRCConversation,
    IRCTurn,
    TopicState,
)
from overblick.plugins.irc.topic_manager import (
    TOPIC_POOL,
    score_identity_interest,
    select_participants,
    select_topic,
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TestIRCTurn:
    def test_create_turn(self):
        turn = IRCTurn(identity="anomal", display_name="Anomal", content="Hello world")
        assert turn.identity == "anomal"
        assert turn.display_name == "Anomal"
        assert turn.content == "Hello world"
        assert turn.turn_number == 0
        assert turn.timestamp > 0

    def test_turn_is_frozen(self):
        turn = IRCTurn(identity="anomal", content="test")
        with pytest.raises(Exception):
            turn.content = "modified"


class TestIRCConversation:
    def test_create_conversation(self):
        conv = IRCConversation(
            id="irc-test123",
            topic="Test Topic",
            participants=["anomal", "cherry"],
        )
        assert conv.id == "irc-test123"
        assert conv.topic == "Test Topic"
        assert conv.state == ConversationState.ACTIVE
        assert conv.is_active is True
        assert conv.turn_count == 0
        assert conv.should_end is False

    def test_conversation_should_end(self):
        turns = [
            IRCTurn(identity="anomal", content=f"msg {i}", turn_number=i)
            for i in range(20)
        ]
        conv = IRCConversation(
            id="irc-full",
            topic="Full",
            participants=["anomal", "cherry"],
            turns=turns,
            max_turns=20,
        )
        assert conv.turn_count == 20
        assert conv.should_end is True

    def test_conversation_states(self):
        conv = IRCConversation(id="irc-states", topic="States", participants=["anomal"])
        assert conv.is_active is True

        paused = conv.model_copy(update={"state": ConversationState.PAUSED})
        assert paused.is_active is False

        completed = conv.model_copy(update={"state": ConversationState.COMPLETED})
        assert completed.is_active is False

        cancelled = conv.model_copy(update={"state": ConversationState.CANCELLED})
        assert cancelled.is_active is False

    def test_conversation_model_dump(self):
        conv = IRCConversation(
            id="irc-dump",
            topic="Dump Test",
            participants=["anomal", "cherry"],
        )
        data = conv.model_dump()
        assert data["id"] == "irc-dump"
        assert data["topic"] == "Dump Test"
        assert data["state"] == "active"
        assert isinstance(data["turns"], list)


class TestTopicState:
    def test_create_topic_state(self):
        ts = TopicState()
        assert ts.available_topics == []
        assert ts.used_topic_ids == []
        assert ts.current_topic_id is None


# ---------------------------------------------------------------------------
# Topic Manager
# ---------------------------------------------------------------------------

class TestTopicPool:
    def test_topic_pool_not_empty(self):
        assert len(TOPIC_POOL) > 0

    def test_topics_have_required_fields(self):
        for topic in TOPIC_POOL:
            assert "id" in topic
            assert "topic" in topic
            assert "tags" in topic
            assert isinstance(topic["tags"], list)
            assert len(topic["tags"]) > 0

    def test_topic_ids_unique(self):
        ids = [t["id"] for t in TOPIC_POOL]
        assert len(ids) == len(set(ids))


class TestSelectTopic:
    def test_selects_topic(self):
        topic = select_topic()
        assert topic is not None
        assert "id" in topic
        assert "topic" in topic

    def test_avoids_used_topics(self):
        used = [t["id"] for t in TOPIC_POOL[:-1]]
        topic = select_topic(used)
        assert topic is not None
        assert topic["id"] not in used

    def test_resets_when_all_used(self):
        all_ids = [t["id"] for t in TOPIC_POOL]
        topic = select_topic(all_ids)
        assert topic is not None  # Should reset and pick from full pool

    def test_empty_used_list(self):
        topic = select_topic([])
        assert topic is not None


class TestScoreIdentityInterest:
    def test_high_score_with_matching_tags(self, mock_identities):
        anomal = mock_identities[0]  # AI, consciousness, philosophy, technology
        topic = {"tags": ["AI", "consciousness", "philosophy"]}
        score = score_identity_interest(anomal, topic)
        assert score > 0.5

    def test_low_score_with_no_matching_tags(self, mock_identities):
        cherry = mock_identities[1]  # relationships, psychology, art, dreams
        topic = {"tags": ["crypto", "economics", "decentralization"]}
        score = score_identity_interest(cherry, topic)
        assert score < 0.2

    def test_zero_score_with_empty_tags(self, mock_identities):
        anomal = mock_identities[0]
        topic = {"tags": []}
        score = score_identity_interest(anomal, topic)
        assert score == 0.0

    def test_score_bounded_zero_to_one(self, mock_identities):
        for identity in mock_identities:
            for topic in TOPIC_POOL:
                score = score_identity_interest(identity, topic)
                assert 0.0 <= score <= 1.0


class TestSelectParticipants:
    def test_selects_minimum_participants(self, mock_identities):
        topic = TOPIC_POOL[0]
        result = select_participants(mock_identities, topic, min_participants=2)
        assert len(result) >= 2

    def test_respects_max_participants(self, mock_identities):
        topic = TOPIC_POOL[0]
        result = select_participants(mock_identities, topic, max_participants=3)
        assert len(result) <= 3

    def test_excludes_supervisor(self):
        supervisor = MagicMock()
        supervisor.name = "supervisor"
        supervisor.interest_keywords = ["everything"]
        supervisor.interests = {}

        regular = MagicMock()
        regular.name = "anomal"
        regular.interest_keywords = ["AI", "philosophy"]
        regular.interests = {}

        result = select_participants([supervisor, regular], TOPIC_POOL[0])
        names = [r.name for r in result]
        assert "supervisor" not in names

    def test_fills_with_random_if_insufficient_interest(self):
        # Create identities with zero interest keywords
        identities = []
        for name in ["a", "b", "c"]:
            ident = MagicMock()
            ident.name = name
            ident.interest_keywords = []
            ident.interests = {}
            identities.append(ident)

        result = select_participants(identities, TOPIC_POOL[0], min_participants=2)
        assert len(result) >= 2


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class TestIRCPlugin:
    @pytest.mark.asyncio
    async def test_setup_creates_data_dir(self, irc_plugin, mock_ctx):
        with patch("overblick.identities.list_identities", return_value=[]), \
             patch("overblick.identities.load_identity"):
            await irc_plugin.setup()

        assert mock_ctx.data_dir.exists()

    @pytest.mark.asyncio
    async def test_setup_loads_identities(self, irc_plugin, mock_ctx):
        mock_identity = MagicMock()
        mock_identity.name = "anomal"

        with patch("overblick.identities.list_identities", return_value=["anomal"]), \
             patch("overblick.identities.load_identity", return_value=mock_identity):
            await irc_plugin.setup()

        assert "anomal" in irc_plugin._identities

    @pytest.mark.asyncio
    async def test_setup_sets_running_true(self, irc_plugin, mock_ctx):
        """setup() activates _running since orchestrator never calls start()."""
        mock_identity = MagicMock()
        mock_identity.name = "anomal"

        with patch("overblick.identities.list_identities", return_value=["anomal"]), \
             patch("overblick.identities.load_identity", return_value=mock_identity):
            await irc_plugin.setup()

        assert irc_plugin._running is True

    @pytest.mark.asyncio
    async def test_teardown_stops_running(self, irc_plugin, mock_ctx):
        irc_plugin._running = True
        await irc_plugin.teardown()
        assert irc_plugin._running is False

    @pytest.mark.asyncio
    async def test_teardown_cancels_active_conversation(self, irc_plugin, mock_ctx):
        irc_plugin._data_dir = mock_ctx.data_dir / "irc"
        irc_plugin._data_dir.mkdir(parents=True, exist_ok=True)
        irc_plugin._running = True

        conv = IRCConversation(
            id="irc-active",
            topic="Active",
            participants=["anomal"],
        )
        irc_plugin._current_conversation = conv

        await irc_plugin.teardown()
        assert irc_plugin._current_conversation.state == ConversationState.CANCELLED

    def test_get_conversations_empty(self, irc_plugin):
        result = irc_plugin.get_conversations()
        assert result == []

    def test_get_conversations_sorted(self, irc_plugin):
        irc_plugin._conversations = [
            IRCConversation(id="older", topic="Old", updated_at=1000),
            IRCConversation(id="newer", topic="New", updated_at=2000),
        ]
        result = irc_plugin.get_conversations()
        assert result[0]["id"] == "newer"
        assert result[1]["id"] == "older"

    def test_get_conversation_by_id(self, irc_plugin):
        conv = IRCConversation(id="irc-find", topic="Find Me")
        irc_plugin._conversations = [conv]
        result = irc_plugin.get_conversation("irc-find")
        assert result is not None
        assert result["topic"] == "Find Me"

    def test_get_conversation_not_found(self, irc_plugin):
        result = irc_plugin.get_conversation("nonexistent")
        assert result is None

    def test_get_current_conversation(self, irc_plugin):
        conv = IRCConversation(id="irc-current", topic="Current")
        irc_plugin._current_conversation = conv
        result = irc_plugin.get_current_conversation()
        assert result is not None
        assert result["id"] == "irc-current"

    def test_get_current_conversation_none(self, irc_plugin):
        result = irc_plugin.get_current_conversation()
        assert result is None


class TestIRCPluginSpeakerSelection:
    def test_first_speaker_is_first_participant(self, irc_plugin):
        irc_plugin._current_conversation = IRCConversation(
            id="irc-speak",
            topic="Speaker",
            participants=["anomal", "cherry", "bjork"],
        )
        speaker = irc_plugin._select_next_speaker()
        assert speaker == "anomal"

    def test_avoids_same_speaker_twice(self, irc_plugin):
        turns = [IRCTurn(identity="anomal", content="test")]
        irc_plugin._current_conversation = IRCConversation(
            id="irc-speak2",
            topic="Speaker2",
            participants=["anomal", "cherry"],
            turns=turns,
        )
        speaker = irc_plugin._select_next_speaker()
        assert speaker != "anomal"
        assert speaker == "cherry"

    def test_no_conversation_returns_none(self, irc_plugin):
        assert irc_plugin._select_next_speaker() is None

    def test_no_participants_returns_none(self, irc_plugin):
        irc_plugin._current_conversation = IRCConversation(
            id="irc-empty",
            topic="Empty",
            participants=[],
        )
        assert irc_plugin._select_next_speaker() is None


class TestIRCPluginStorage:
    def test_save_and_load_conversation(self, irc_plugin, mock_ctx):
        data_dir = mock_ctx.data_dir / "irc"
        data_dir.mkdir(parents=True, exist_ok=True)
        irc_plugin._data_dir = data_dir

        conv = IRCConversation(
            id="irc-save",
            topic="Save Test",
            participants=["anomal", "cherry"],
            turns=[
                IRCTurn(identity="anomal", display_name="Anomal", content="Hello"),
                IRCTurn(identity="cherry", display_name="Cherry", content="Hi there"),
            ],
        )
        irc_plugin._save_conversation(conv)

        # Verify file exists
        assert (data_dir / "conversations.json").exists()

        # Load and verify
        loaded = irc_plugin._load_conversations()
        assert len(loaded) == 1
        assert loaded[0].id == "irc-save"
        assert loaded[0].turn_count == 2

    def test_save_trims_old_conversations(self, irc_plugin, mock_ctx):
        data_dir = mock_ctx.data_dir / "irc"
        data_dir.mkdir(parents=True, exist_ok=True)
        irc_plugin._data_dir = data_dir

        # Fill with 50 conversations
        for i in range(50):
            conv = IRCConversation(
                id=f"irc-old-{i}",
                topic=f"Old {i}",
                updated_at=float(i),
            )
            irc_plugin._save_conversation(conv)

        # Add one more
        new_conv = IRCConversation(
            id="irc-new",
            topic="New",
            updated_at=999999.0,
        )
        irc_plugin._save_conversation(new_conv)

        # Should be capped at 50
        assert len(irc_plugin._conversations) <= 50
        # Newest should be present
        ids = [c.id for c in irc_plugin._conversations]
        assert "irc-new" in ids

    def test_load_handles_missing_file(self, irc_plugin, mock_ctx):
        data_dir = mock_ctx.data_dir / "irc"
        data_dir.mkdir(parents=True, exist_ok=True)
        irc_plugin._data_dir = data_dir
        result = irc_plugin._load_conversations()
        assert result == []

    def test_load_handles_corrupt_json(self, irc_plugin, mock_ctx):
        data_dir = mock_ctx.data_dir / "irc"
        data_dir.mkdir(parents=True, exist_ok=True)
        irc_plugin._data_dir = data_dir
        (data_dir / "conversations.json").write_text("not valid json{{{")
        result = irc_plugin._load_conversations()
        assert result == []


class TestIRCPluginConversationTick:
    @pytest.mark.asyncio
    async def test_tick_skips_quiet_hours(self, irc_plugin, mock_ctx):
        irc_plugin._running = True
        # IRC uses its own quiet hours (23:00-07:00), not the global checker
        with patch.object(irc_plugin, "_is_irc_quiet_hours", return_value=True):
            await irc_plugin._conversation_tick()
        # Should not start any conversation
        assert irc_plugin._current_conversation is None

    @pytest.mark.asyncio
    async def test_tick_runs_outside_irc_quiet_hours(self, irc_plugin, mock_ctx):
        """IRC should run between 07:00-23:00 even if global quiet hours are active."""
        irc_plugin._running = True
        irc_plugin._data_dir = mock_ctx.data_dir / "irc"
        irc_plugin._data_dir.mkdir(parents=True, exist_ok=True)
        # Global quiet hours active (21-07), but IRC's own quiet hours not active
        mock_ctx.quiet_hours_checker.is_quiet_hours.return_value = True
        with patch.object(irc_plugin, "_is_irc_quiet_hours", return_value=False), \
             patch.object(irc_plugin, "_is_system_idle", new_callable=AsyncMock, return_value=True), \
             patch.object(irc_plugin, "_start_conversation", new_callable=AsyncMock) as mock_start:
            await irc_plugin._conversation_tick()
        # Should attempt to start a conversation
        mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_tick_skips_when_not_running(self, irc_plugin):
        irc_plugin._running = False
        await irc_plugin._conversation_tick()
        assert irc_plugin._current_conversation is None

    @pytest.mark.asyncio
    async def test_tick_pauses_on_high_load(self, irc_plugin, mock_ctx):
        irc_plugin._running = True
        irc_plugin._data_dir = mock_ctx.data_dir / "irc"
        irc_plugin._data_dir.mkdir(parents=True, exist_ok=True)

        conv = IRCConversation(
            id="irc-loaded",
            topic="High Load",
            participants=["anomal"],
        )
        irc_plugin._current_conversation = conv

        with patch.object(irc_plugin, "_is_irc_quiet_hours", return_value=False), \
             patch.object(irc_plugin, "_is_system_idle", new_callable=AsyncMock, return_value=False):
            await irc_plugin._conversation_tick()

        assert irc_plugin._current_conversation.state == ConversationState.PAUSED


class TestIRCPluginSystemCheck:
    @pytest.mark.asyncio
    async def test_is_system_idle_default(self, irc_plugin):
        """When inspection fails, defaults to idle (True)."""
        with patch(
            "overblick.capabilities.monitoring.inspector.HostInspectionCapability"
        ) as mock_cap:
            mock_cap.side_effect = ImportError("not available")
            result = await irc_plugin._is_system_idle()
        assert result is True
