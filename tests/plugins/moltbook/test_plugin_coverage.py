"""
Additional tests for plugin.py to achieve 100% line coverage.

Covers uncovered paths: setup error, tick circuit breaker, suspension handling,
DM handling, heartbeat, dream journal, capability setup, status persistence,
dream date persistence/loading, engage_with_post details, reply handling,
own comment/post reply checking, and fallback prompts.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from overblick.plugins.moltbook.client import MoltbookError, RateLimitError, SuspensionError
from overblick.plugins.moltbook.models import Comment, Post
from overblick.plugins.moltbook.plugin import MoltbookPlugin, _FallbackPrompts


class _MockIdentity:
    def __init__(self, name="testbot", enabled_modules=None):
        self.name = name
        self.display_name = name.title()
        self.description = "Test identity"
        self.engagement_threshold = 35
        self.enabled_modules = enabled_modules or ()
        self.llm = MagicMock(temperature=0.7, max_tokens=2000, model="test")
        self.quiet_hours = MagicMock()
        self.schedule = MagicMock()
        self.security = MagicMock()
        self.interest_keywords = ["ai", "test"]
        self.identity_dir = Path("/tmp/nonexistent")
        self.raw_config = {
            "agent_name": name.title(),
            "engagement_threshold": 35.0,
            "interest_keywords": ["ai", "test"],
            "enabled_modules": list(enabled_modules or []),
        }

    def __getattr__(self, name):
        if name in self.__dict__:
            return self.__dict__[name]
        raise AttributeError(name)


def _make_ctx(tmp_path, identity=None, **overrides):
    """Build a minimal PluginContext mock."""
    identity = identity or _MockIdentity()
    ctx = MagicMock()
    ctx.identity = identity
    ctx.identity_name = identity.name
    ctx.data_dir = tmp_path / "data" / identity.name
    ctx.log_dir = tmp_path / "logs" / identity.name
    ctx.llm_pipeline = overrides.get("llm_pipeline", AsyncMock())
    ctx.llm_client = overrides.get("llm_client", AsyncMock())
    ctx.audit_log = overrides.get("audit_log", MagicMock())
    ctx.engagement_db = overrides.get("engagement_db", AsyncMock())
    ctx.quiet_hours_checker = overrides.get("quiet_hours_checker", MagicMock(is_quiet_hours=MagicMock(return_value=False)))
    ctx.event_bus = MagicMock()
    ctx.scheduler = MagicMock()
    ctx.preflight_checker = AsyncMock()
    ctx.output_safety = AsyncMock()
    ctx.learning_store = overrides.get("learning_store", None)
    ctx.capabilities = overrides.get("capabilities", {})

    def get_secret(key):
        return {
            "moltbook_api_key": "test-key",
            "moltbook_agent_id": "agent-001",
        }.get(key)

    ctx.get_secret = get_secret
    return ctx


def _make_plugin(ctx):
    """Create plugin WITHOUT running setup."""
    return MoltbookPlugin(ctx)


async def _setup_plugin(plugin, ctx, mock_client=None):
    """Setup plugin with mocked components."""
    mock_client = mock_client or AsyncMock()
    with patch("overblick.plugins.moltbook.plugin.MoltbookClient", return_value=mock_client):
        with patch.object(plugin, "_load_prompts", return_value=_FallbackPrompts()):
            with patch("overblick.plugins.moltbook.plugin.CapabilityRegistry") as MockReg:
                mock_registry = MagicMock()
                mock_registry.resolve = MagicMock(return_value=[])
                MockReg.default = MagicMock(return_value=mock_registry)
                await plugin.setup()
    plugin._client = mock_client
    return plugin


class TestSetupErrors:
    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self, tmp_path):
        """Line 108: Missing API key raises RuntimeError."""
        ctx = _make_ctx(tmp_path)
        ctx.get_secret = lambda key: None
        plugin = _make_plugin(ctx)
        with pytest.raises(RuntimeError, match="Missing moltbook_api_key"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_no_llm_pipeline(self, tmp_path):
        """Line 127: No LLM pipeline logs warning."""
        ctx = _make_ctx(tmp_path, llm_pipeline=None)
        plugin = _make_plugin(ctx)
        await _setup_plugin(plugin, ctx)
        assert plugin._challenge_handler is None


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_get_status_with_client(self, tmp_path):
        """Lines 208-210: get_status returns client status."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.get_account_status = MagicMock(return_value={"status": "active"})
        await _setup_plugin(plugin, ctx, mock_client)
        status = plugin.get_status()
        assert status["status"] == "active"

    def test_get_status_no_client(self, tmp_path):
        """Line 210: No client returns unknown."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        status = plugin.get_status()
        assert status["status"] == "unknown"


class TestPersistStatus:
    @pytest.mark.asyncio
    async def test_persist_status(self, tmp_path):
        """Lines 215-224: Status persisted to disk."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.get_account_status = MagicMock(return_value={"status": "active"})
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._persist_status()
        status_file = ctx.data_dir / "moltbook_status.json"
        assert status_file.exists()

    def test_persist_status_no_client(self, tmp_path):
        """Line 215: No client, returns early."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        plugin._persist_status()  # Should not raise

    def test_persist_status_non_path_data_dir(self, tmp_path):
        """Line 219: data_dir is not a Path."""
        ctx = _make_ctx(tmp_path)
        ctx.data_dir = "/tmp/string/path"
        plugin = _make_plugin(ctx)
        plugin._client = MagicMock()
        plugin._persist_status()  # Should not raise


class TestDreamDatePersistence:
    def test_persist_dream_date(self, tmp_path):
        """Lines 229-244: Dream date persisted."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        plugin._dream_journal_posted_date = date(2026, 3, 15)
        plugin._persist_dream_date()
        state_file = ctx.data_dir / "dream_state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["dream_journal_posted_date"] == "2026-03-15"

    def test_persist_dream_date_none(self, tmp_path):
        """Line 229: No dream date, returns early."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        plugin._persist_dream_date()  # Should not raise

    def test_persist_dream_date_non_path(self, tmp_path):
        """Line 234: Non-Path data_dir."""
        ctx = _make_ctx(tmp_path)
        ctx.data_dir = "string_path"
        plugin = _make_plugin(ctx)
        plugin._dream_journal_posted_date = date.today()
        plugin._persist_dream_date()  # Should not raise

    def test_load_dream_date(self, tmp_path):
        """Lines 251-260: Dream date loaded from disk."""
        ctx = _make_ctx(tmp_path)
        state_dir = ctx.data_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / "dream_state.json"
        state_file.write_text(json.dumps({"dream_journal_posted_date": "2026-03-10"}))
        plugin = _make_plugin(ctx)
        plugin._load_dream_date()
        assert plugin._dream_journal_posted_date == date(2026, 3, 10)

    def test_load_dream_date_no_file(self, tmp_path):
        """Line 255: No state file."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        plugin._load_dream_date()
        assert plugin._dream_journal_posted_date is None

    def test_load_dream_date_non_path(self, tmp_path):
        """Line 251: Non-Path data_dir."""
        ctx = _make_ctx(tmp_path)
        ctx.data_dir = "string_path"
        plugin = _make_plugin(ctx)
        plugin._load_dream_date()  # Should not raise

    def test_load_dream_date_corrupt(self, tmp_path):
        """Line 260: Corrupt state file."""
        ctx = _make_ctx(tmp_path)
        state_dir = ctx.data_dir
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "dream_state.json").write_text("not json")
        plugin = _make_plugin(ctx)
        plugin._load_dream_date()  # Should not raise


class TestTick:
    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_tick(self, tmp_path):
        """Lines 275-283: Circuit breaker skips tick."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._consecutive_api_failures = 5
        plugin._tick_count = 1  # tick_count % backoff != 0
        await plugin.tick()
        mock_client.get_posts.assert_not_called()

    @pytest.mark.asyncio
    async def test_suspension_skips_tick(self, tmp_path):
        """Lines 286-289: Suspension backoff skips tick."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._suspended_until = datetime.now(UTC) + timedelta(hours=1)
        await plugin.tick()
        mock_client.get_posts.assert_not_called()

    @pytest.mark.asyncio
    async def test_quiet_hours_skips_tick(self, tmp_path):
        """Lines 292-294: Quiet hours skips tick."""
        ctx = _make_ctx(tmp_path, quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=True)))
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        await plugin.tick()
        mock_client.get_posts.assert_not_called()

    @pytest.mark.asyncio
    async def test_mood_cycle_threshold(self, tmp_path):
        """Lines 302-304: Mood cycle modifies threshold."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.get_posts = AsyncMock(return_value=[])
        await _setup_plugin(plugin, ctx, mock_client)
        # Add mock mood cycle capability
        mood_cap = MagicMock()
        mood_cap.get_threshold_offset = MagicMock(return_value=5.0)
        plugin._capabilities["mood_cycle"] = mood_cap
        await plugin.tick()

    @pytest.mark.asyncio
    async def test_tick_suspension_error_with_expiry(self, tmp_path):
        """Lines 376-394: SuspensionError with expiry timestamp."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        exc = SuspensionError("suspended until 2026-04-01T00:00:00Z")
        mock_client.get_posts = AsyncMock(side_effect=exc)
        await _setup_plugin(plugin, ctx, mock_client)
        await plugin.tick()
        assert plugin._suspended_until is not None

    @pytest.mark.asyncio
    async def test_tick_suspension_error_no_expiry(self, tmp_path):
        """Lines 388-394: SuspensionError without expiry."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        exc = SuspensionError("Account suspended", reason="spam")
        mock_client.get_posts = AsyncMock(side_effect=exc)
        await _setup_plugin(plugin, ctx, mock_client)
        await plugin.tick()
        assert plugin._suspended_until is not None

    @pytest.mark.asyncio
    async def test_tick_upvote_fails(self, tmp_path):
        """Lines 353-356: Upvote fails with MoltbookError."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test", content="Interesting stuff about ai")
        mock_client.get_posts = AsyncMock(return_value=[post])
        mock_client.upvote_post = AsyncMock(side_effect=MoltbookError("fail"))
        await _setup_plugin(plugin, ctx, mock_client)
        # Make decision engine return upvote
        plugin._decision_engine.evaluate_post = MagicMock(
            return_value=MagicMock(action="upvote", score=40, hostile=False)
        )
        await plugin.tick()  # Should not raise

    @pytest.mark.asyncio
    async def test_tick_moltbook_error(self, tmp_path):
        """Lines 406-414: MoltbookError increments failure count."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.get_posts = AsyncMock(side_effect=MoltbookError("error"))
        await _setup_plugin(plugin, ctx, mock_client)
        await plugin.tick()
        assert plugin._consecutive_api_failures == 1

    @pytest.mark.asyncio
    async def test_tick_unexpected_error(self, tmp_path):
        """Lines 415-422: Unexpected error increments failure count."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.get_posts = AsyncMock(side_effect=RuntimeError("unexpected"))
        await _setup_plugin(plugin, ctx, mock_client)
        await plugin.tick()
        assert plugin._consecutive_api_failures == 1


class TestEngageWithPost:
    @pytest.mark.asyncio
    async def test_engage_with_empty_response(self, tmp_path):
        """Line 479-481: Empty response skips."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test", content="Content")
        decision = MagicMock(score=50)
        await plugin._engage_with_post(post, decision)
        mock_client.create_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_engage_with_learning_store(self, tmp_path):
        """Lines 447-456: Learning store context added."""
        learning_store = AsyncMock()
        learning_store.get_relevant = AsyncMock(return_value=[
            MagicMock(content="AI is great"),
        ])
        ctx = _make_ctx(tmp_path, learning_store=learning_store)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_comment = AsyncMock(return_value=Comment(
            id="c1", post_id="p1", agent_id="a1", agent_name="Test", content="Reply",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Great post!")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test", content="Content")
        decision = MagicMock(score=50)
        await plugin._engage_with_post(post, decision)

    @pytest.mark.asyncio
    async def test_engage_learning_extractor(self, tmp_path):
        """Lines 506-510: Learning extractor runs after comment."""
        learning_store = AsyncMock()
        learning_store.get_relevant = AsyncMock(return_value=[])
        learning_store.propose = AsyncMock()
        ctx = _make_ctx(tmp_path, learning_store=learning_store)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_comment = AsyncMock(return_value=Comment(
            id="c1", post_id="p1", agent_id="a1", agent_name="Test", content="Reply",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Great post!")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="AI systems can learn patterns from data through neural networks.")
        decision = MagicMock(score=50)
        await plugin._engage_with_post(post, decision)

    @pytest.mark.asyncio
    async def test_engage_upvotes_other_comments(self, tmp_path):
        """Lines 523-536: Upvote other non-hostile comments."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        other_comment = Comment(id="c2", post_id="p1", agent_id="a3", agent_name="Bot3", content="Nice")
        mock_client.create_comment = AsyncMock(return_value=Comment(
            id="c1", post_id="p1", agent_id="a1", agent_name="Test", content="Reply",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Reply")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[other_comment])
        decision = MagicMock(score=50)
        await plugin._engage_with_post(post, decision)

    @pytest.mark.asyncio
    async def test_engage_rate_limit(self, tmp_path):
        """Lines 540-541: Rate limit on comment."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_comment = AsyncMock(side_effect=RateLimitError("limited"))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Reply")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test", content="Content")
        decision = MagicMock(score=50)
        await plugin._engage_with_post(post, decision)

    @pytest.mark.asyncio
    async def test_engage_moltbook_error(self, tmp_path):
        """Lines 542-543: MoltbookError on comment."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_comment = AsyncMock(side_effect=MoltbookError("fail"))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Reply")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test", content="Content")
        decision = MagicMock(score=50)
        await plugin._engage_with_post(post, decision)


class TestCheckOwnCommentReplies:
    @pytest.mark.asyncio
    async def test_check_own_comment_replies(self, tmp_path):
        """Lines 649-709: Full path through own comment reply checking."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        reply = Comment(id="c3", post_id="p1", agent_id="a3", agent_name="Bot3",
                        content="Reply to you", parent_id="c1")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[reply])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.get_my_comment_ids_for_post = AsyncMock(return_value=["c1"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        plugin._decision_engine = MagicMock()
        plugin._decision_engine.evaluate_reply = MagicMock(
            return_value=MagicMock(hostile=False, should_engage=True, score=50)
        )
        await plugin._check_own_comment_replies()

    @pytest.mark.asyncio
    async def test_check_own_comment_replies_hostile(self, tmp_path):
        """Lines 678-685: Hostile reply skipped."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        reply = Comment(id="c3", post_id="p1", agent_id="a3", agent_name="Bot3",
                        content="Spam", parent_id="c1")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[reply])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.get_my_comment_ids_for_post = AsyncMock(return_value=["c1"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        plugin._decision_engine = MagicMock()
        plugin._decision_engine.evaluate_reply = MagicMock(
            return_value=MagicMock(hostile=True, should_engage=False, score=0)
        )
        await plugin._check_own_comment_replies()

    @pytest.mark.asyncio
    async def test_check_own_comment_replies_skip_engage(self, tmp_path):
        """Lines 700-706: Non-hostile, non-engaging reply → skip."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        reply = Comment(id="c3", post_id="p1", agent_id="a3", agent_name="Bot3",
                        content="ok", parent_id="c1")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[reply])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.get_my_comment_ids_for_post = AsyncMock(return_value=["c1"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        plugin._decision_engine = MagicMock()
        plugin._decision_engine.evaluate_reply = MagicMock(
            return_value=MagicMock(hostile=False, should_engage=False, score=10)
        )
        await plugin._check_own_comment_replies()

    @pytest.mark.asyncio
    async def test_check_own_comment_replies_error(self, tmp_path):
        """Lines 636-638: Error getting comment post IDs."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(side_effect=RuntimeError("db err"))
        await plugin._check_own_comment_replies()

    @pytest.mark.asyncio
    async def test_check_own_comment_replies_empty(self, tmp_path):
        """Line 641: No post IDs."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=[])
        await plugin._check_own_comment_replies()

    @pytest.mark.asyncio
    async def test_check_own_comment_replies_moltbook_error(self, tmp_path):
        """Line 709: MoltbookError during check."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.get_post = AsyncMock(side_effect=MoltbookError("fail"))
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.get_my_comment_ids_for_post = AsyncMock(return_value=["c1"])
        await plugin._check_own_comment_replies()


class TestHandleDMs:
    @pytest.mark.asyncio
    async def test_dm_re_enable_after_cooldown(self, tmp_path):
        """Lines 720-723: DMs re-enabled after cooldown."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.list_dm_requests = AsyncMock(return_value=[])
        mock_client.list_conversations = AsyncMock(return_value=[])
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._dms_supported = False
        plugin._dm_disabled_until = datetime.now(UTC) - timedelta(hours=1)
        await plugin._handle_dms()
        assert plugin._dms_supported is True


class TestHandleMoltcaptcha:
    @pytest.mark.asyncio
    async def test_moltcaptcha_failed(self, tmp_path):
        """Line 833: MoltCaptcha solving failed."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="@Testbot MoltCaptcha: invalid challenge content xyz")
        with patch("overblick.plugins.moltbook.plugin.MoltCaptchaSolver") as MockSolver:
            solver = MagicMock()
            solver.parse_challenge = MagicMock(return_value={"type": "test"})
            solver.solve = MagicMock(return_value=None)
            MockSolver.return_value = solver
            await plugin._handle_moltcaptcha("p1", post)


class TestHandleReply:
    @pytest.mark.asyncio
    async def test_handle_reply_with_learnings(self, tmp_path):
        """Lines 869-878: Reply handler with learning store."""
        learning_store = AsyncMock()
        learning_store.get_relevant = AsyncMock(return_value=[MagicMock(content="fact")])
        ctx = _make_ctx(tmp_path, learning_store=learning_store)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        comment = Comment(id="c1", post_id="p1", agent_id="a2", agent_name="Other", content="Reply")
        post = Post(id="p1", agent_id="a1", agent_name="Test", title="Test",
                     content="Content", comments=[comment])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_reply = AsyncMock(return_value="My reply")
        result = await plugin._handle_reply("p1", "c1", "reply", 50)
        assert result is True

    @pytest.mark.asyncio
    async def test_handle_reply_comment_not_found(self, tmp_path):
        """Lines 851-853: Comment not found."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        post = Post(id="p1", agent_id="a1", agent_name="Test", title="Test",
                     content="Content", comments=[])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        result = await plugin._handle_reply("p1", "nonexistent", "reply", 50)
        assert result is False


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_dream_priority(self, tmp_path):
        """Lines 911-914: Heartbeat skipped for pending dream."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        dream_system = MagicMock()
        dream_system.last_dream = MagicMock()
        plugin._dream_system = dream_system
        plugin._dream_journal_posted_date = None  # Not posted today
        result = await plugin.post_heartbeat()
        assert result is False

    @pytest.mark.asyncio
    async def test_heartbeat_learning_based(self, tmp_path):
        """Lines 923-951: Learning-based heartbeat prompt."""
        learning_store = AsyncMock()
        learning_store.get_approved = AsyncMock(return_value=[MagicMock(content="fact1")])
        ctx = _make_ctx(tmp_path, learning_store=learning_store)
        ctx.engagement_db.get_recent_interactions = AsyncMock(return_value=[])
        ctx.engagement_db.get_todays_heartbeat_titles = AsyncMock(return_value=[])
        ctx.engagement_db.get_recent_heartbeat_titles = AsyncMock(return_value=[])
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(return_value=Post(
            id="p1", agent_id="a1", agent_name="Test", title="Title", content="Content",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_heartbeat = AsyncMock(return_value=("Title", "Content", "ai"))
        plugin._prompts = MagicMock()
        plugin._prompts.LEARNING_BASED_HEARTBEAT_PROMPT = "Write about {learnings} and {interactions}"
        plugin._prompts.SYSTEM_PROMPT = "Test"
        plugin._prompts.HEARTBEAT_TOPICS = []
        with patch("random.random", return_value=0.1):  # < 0.3
            result = await plugin.post_heartbeat()
        assert result is True

    @pytest.mark.asyncio
    async def test_heartbeat_topic_submolt_override(self, tmp_path):
        """Lines 1019-1021: Topic submolt overrides default."""
        ctx = _make_ctx(tmp_path)
        ctx.engagement_db.get_todays_heartbeat_titles = AsyncMock(return_value=[])
        ctx.engagement_db.get_recent_heartbeat_titles = AsyncMock(return_value=[])
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(return_value=Post(
            id="p1", agent_id="a1", agent_name="Test", title="Title", content="Content",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_heartbeat = AsyncMock(return_value=("Title", "Content", "ai"))
        plugin._prompts = MagicMock()
        plugin._prompts.LEARNING_BASED_HEARTBEAT_PROMPT = None
        plugin._prompts.SYSTEM_PROMPT = "Test"
        plugin._prompts.HEARTBEAT_PROMPT = "Write about {topic_index}"
        plugin._prompts.HEARTBEAT_TOPICS = [{"id": "t1", "instruction": "test", "submolt": "crypto"}]
        await plugin.post_heartbeat()
        # Verify submolt was overridden
        call_args = mock_client.create_post.call_args
        assert call_args[1]["submolt"] == "crypto" or call_args[0][2] == "crypto"

    @pytest.mark.asyncio
    async def test_heartbeat_rate_limit(self, tmp_path):
        """Lines 1053-1054: Heartbeat rate limited."""
        ctx = _make_ctx(tmp_path)
        ctx.engagement_db.get_todays_heartbeat_titles = AsyncMock(return_value=[])
        ctx.engagement_db.get_recent_heartbeat_titles = AsyncMock(return_value=[])
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(side_effect=RateLimitError("limited"))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_heartbeat = AsyncMock(return_value=("Title", "Content", "ai"))
        result = await plugin.post_heartbeat()
        assert result is False

    @pytest.mark.asyncio
    async def test_heartbeat_moltbook_error(self, tmp_path):
        """Lines 1053-1054: Heartbeat MoltbookError."""
        ctx = _make_ctx(tmp_path)
        ctx.engagement_db.get_todays_heartbeat_titles = AsyncMock(return_value=[])
        ctx.engagement_db.get_recent_heartbeat_titles = AsyncMock(return_value=[])
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(side_effect=MoltbookError("fail"))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_heartbeat = AsyncMock(return_value=("Title", "Content", "ai"))
        result = await plugin.post_heartbeat()
        assert result is False

    @pytest.mark.asyncio
    async def test_heartbeat_title_exception(self, tmp_path):
        """Lines 996-997: Exception fetching heartbeat titles."""
        ctx = _make_ctx(tmp_path)
        ctx.engagement_db.get_todays_heartbeat_titles = AsyncMock(side_effect=RuntimeError("db err"))
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(return_value=Post(
            id="p1", agent_id="a1", agent_name="Test", title="Title", content="Content",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_heartbeat = AsyncMock(return_value=("Title", "Content", "ai"))
        result = await plugin.post_heartbeat()
        assert result is True


class TestDreamJournal:
    @pytest.mark.asyncio
    async def test_dream_journal_generation_error(self, tmp_path):
        """Lines 1106-1108: Dream journal generation error."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        dream_system = MagicMock()
        dream_system.last_dream = MagicMock()
        dream_system.last_dream.to_dict = MagicMock(return_value={"text": "dream"})
        plugin._dream_system = dream_system
        plugin._prompts = MagicMock()
        plugin._prompts.DREAM_JOURNAL_PROMPT = "Write about {dream}"
        plugin._prompts.SUBMOLT_INSTRUCTION = ""
        plugin._prompts.SYSTEM_PROMPT = "Test"
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_dream_post = AsyncMock(side_effect=RuntimeError("gen fail"))
        result = await plugin._maybe_post_dream_journal()
        assert result is False

    @pytest.mark.asyncio
    async def test_dream_journal_rate_limit(self, tmp_path):
        """Lines 1136-1137: Dream journal rate limited."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(side_effect=RateLimitError("limited"))
        await _setup_plugin(plugin, ctx, mock_client)
        dream_system = MagicMock()
        dream_system.last_dream = MagicMock()
        dream_system.last_dream.to_dict = MagicMock(return_value={"text": "dream"})
        plugin._dream_system = dream_system
        plugin._prompts = MagicMock()
        plugin._prompts.DREAM_JOURNAL_PROMPT = "Write about {dream}"
        plugin._prompts.SUBMOLT_INSTRUCTION = ""
        plugin._prompts.SYSTEM_PROMPT = "Test"
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_dream_post = AsyncMock(return_value=("Title", "Content", "ai"))
        result = await plugin._maybe_post_dream_journal()
        assert result is False


class TestCapabilitySetup:
    @pytest.mark.asyncio
    async def test_capability_registry_error(self, tmp_path):
        """Lines 1171-1174: CapabilityRegistry.default() raises."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        with patch("overblick.plugins.moltbook.plugin.CapabilityRegistry") as MockReg:
            MockReg.default = MagicMock(side_effect=RuntimeError("no registry"))
            await plugin._setup_capabilities([], "system prompt")

    @pytest.mark.asyncio
    async def test_shared_capabilities_loaded(self, tmp_path):
        """Lines 1163-1166: Shared capabilities from orchestrator."""
        ctx = _make_ctx(tmp_path)
        shared_cap = MagicMock()
        shared_cap.name = "test_cap"
        ctx.capabilities = {"test_cap": shared_cap}
        plugin = _make_plugin(ctx)
        with patch("overblick.plugins.moltbook.plugin.CapabilityRegistry") as MockReg:
            mock_registry = MagicMock()
            mock_registry.resolve = MagicMock(return_value=[])
            MockReg.default = MagicMock(return_value=mock_registry)
            await plugin._setup_capabilities([], "system prompt")
        assert "test_cap" in plugin._capabilities

    @pytest.mark.asyncio
    async def test_capability_setup_failure(self, tmp_path):
        """Lines 1194-1195: Capability setup failure logged."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        with patch("overblick.plugins.moltbook.plugin.CapabilityRegistry") as MockReg:
            mock_registry = MagicMock()
            mock_registry.resolve = MagicMock(return_value=["bad_cap"])
            bad_cap = MagicMock()
            bad_cap.setup = AsyncMock(side_effect=RuntimeError("setup fail"))
            mock_registry.create = MagicMock(return_value=bad_cap)
            MockReg.default = MagicMock(return_value=mock_registry)
            with patch("overblick.core.capability.build_capability_configs", return_value={}):
                await plugin._setup_capabilities(["bad_cap"], "system prompt")

    @pytest.mark.asyncio
    async def test_capability_already_loaded(self, tmp_path):
        """Lines 1186-1187: Capability already loaded from shared."""
        ctx = _make_ctx(tmp_path)
        existing_cap = MagicMock()
        existing_cap.name = "dream_system"
        plugin = _make_plugin(ctx)
        plugin._capabilities["dream_system"] = existing_cap
        with patch("overblick.plugins.moltbook.plugin.CapabilityRegistry") as MockReg:
            mock_registry = MagicMock()
            mock_registry.resolve = MagicMock(return_value=["dream_system"])
            MockReg.default = MagicMock(return_value=mock_registry)
            with patch("overblick.core.capability.build_capability_configs", return_value={}):
                await plugin._setup_capabilities(["dream_system"], "system prompt")


class TestLoadPrompts:
    def test_load_prompts_fallback(self, tmp_path):
        """Lines 1235-1241: ImportError falls back."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        result = plugin._load_prompts("nonexistent_identity_xyz")
        assert isinstance(result, _FallbackPrompts)

    def test_get_capability(self, tmp_path):
        """Line 1231: Get capability by name."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        cap = MagicMock()
        plugin._capabilities["test"] = cap
        assert plugin.get_capability("test") is cap
        assert plugin.get_capability("missing") is None


class TestTeardown:
    @pytest.mark.asyncio
    async def test_teardown(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        await plugin.teardown()
        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_teardown_capability_error(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        bad_cap = MagicMock()
        bad_cap.name = "bad"
        bad_cap.teardown = AsyncMock(side_effect=RuntimeError("fail"))
        plugin._capabilities["bad"] = bad_cap
        await plugin.teardown()  # Should not raise


class TestTickCapabilities:
    @pytest.mark.asyncio
    async def test_tick_capabilities_error(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        cap = MagicMock()
        cap.name = "bad"
        cap.enabled = True
        cap.tick = AsyncMock(side_effect=RuntimeError("tick fail"))
        plugin._capabilities["bad"] = cap
        await plugin._tick_capabilities()


class TestTickSuspensionFromUpvote:
    @pytest.mark.asyncio
    async def test_upvote_suspension_re_raises(self, tmp_path):
        """Line 354: SuspensionError from upvote propagates to tick handler."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Interesting ai content")
        mock_client.get_posts = AsyncMock(return_value=[post])
        mock_client.upvote_post = AsyncMock(side_effect=SuspensionError("suspended"))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._decision_engine.evaluate_post = MagicMock(
            return_value=MagicMock(action="upvote", score=40)
        )
        await plugin.tick()
        assert plugin._suspended_until is not None

    @pytest.mark.asyncio
    async def test_suspension_naive_timestamp(self, tmp_path):
        """Line 381: Naive suspended_until_dt gets UTC timezone."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        exc = SuspensionError("test", suspended_until="2026-04-01T00:00:00")
        mock_client.get_posts = AsyncMock(side_effect=exc)
        await _setup_plugin(plugin, ctx, mock_client)
        await plugin.tick()
        assert plugin._suspended_until is not None
        assert plugin._suspended_until.tzinfo is not None


class TestReplyQueue:
    @pytest.mark.asyncio
    async def test_reply_queue_processed(self, tmp_path):
        """Line 360: Reply queue processed during tick."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.get_posts = AsyncMock(return_value=[])
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._reply_queue = AsyncMock()
        plugin._reply_queue.process_queue = AsyncMock()
        await plugin.tick()
        plugin._reply_queue.process_queue.assert_called_once()


class TestEngageWithPostExtended:
    @pytest.mark.asyncio
    async def test_learning_store_exception(self, tmp_path):
        """Lines 455-456: Learning store exception in engage."""
        learning_store = AsyncMock()
        learning_store.get_relevant = AsyncMock(side_effect=RuntimeError("db err"))
        ctx = _make_ctx(tmp_path, learning_store=learning_store)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_comment = AsyncMock(return_value=Comment(
            id="c1", post_id="p1", agent_id="a1", agent_name="Test", content="Reply",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Reply")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test", content="Content")
        decision = MagicMock(score=50)
        await plugin._engage_with_post(post, decision)

    @pytest.mark.asyncio
    async def test_upvote_other_comment_hostile(self, tmp_path):
        """Lines 533-536: Skip upvoting hostile comments."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        hostile_comment = Comment(id="c2", post_id="p1", agent_id="a3",
                                   agent_name="Bot3", content="Spam garbage")
        mock_client.create_comment = AsyncMock(return_value=Comment(
            id="c1", post_id="p1", agent_id="a1", agent_name="Test", content="Reply",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Reply")
        plugin._decision_engine.evaluate_reply = MagicMock(
            return_value=MagicMock(hostile=True)
        )
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[hostile_comment])
        decision = MagicMock(score=50)
        await plugin._engage_with_post(post, decision)
        mock_client.upvote_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_upvote_own_comment_skipped(self, tmp_path):
        """Lines 525-526: Own comments not upvoted."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        own_comment = Comment(id="c2", post_id="p1", agent_id="a1",
                              agent_name="Testbot", content="My own comment")
        mock_client.create_comment = AsyncMock(return_value=Comment(
            id="c1", post_id="p1", agent_id="a1", agent_name="Testbot", content="Reply",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Reply")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[own_comment])
        decision = MagicMock(score=50)
        await plugin._engage_with_post(post, decision)
        mock_client.upvote_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_upvote_comment_error_swallowed(self, tmp_path):
        """Lines 535-536: MoltbookError on upvote swallowed."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        other_comment = Comment(id="c2", post_id="p1", agent_id="a3",
                                 agent_name="Bot3", content="Nice post")
        mock_client.create_comment = AsyncMock(return_value=Comment(
            id="c1", post_id="p1", agent_id="a1", agent_name="Testbot", content="Reply",
        ))
        mock_client.upvote_comment = AsyncMock(side_effect=MoltbookError("fail"))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Reply")
        plugin._decision_engine.evaluate_reply = MagicMock(
            return_value=MagicMock(hostile=False)
        )
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[other_comment])
        decision = MagicMock(score=50)
        await plugin._engage_with_post(post, decision)

    @pytest.mark.asyncio
    async def test_comment_no_id_skipped(self, tmp_path):
        """Line 523: Comment without ID skipped."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        no_id_comment = Comment(id="", post_id="p1", agent_id="a3",
                                 agent_name="Bot3", content="No id")
        mock_client.create_comment = AsyncMock(return_value=Comment(
            id="c1", post_id="p1", agent_id="a1", agent_name="Testbot", content="Reply",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Reply")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[no_id_comment])
        decision = MagicMock(score=50)
        await plugin._engage_with_post(post, decision)
        mock_client.upvote_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_engage_suspension_re_raises(self, tmp_path):
        """Lines 538-539: SuspensionError re-raised."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_comment = AsyncMock(side_effect=SuspensionError("suspended"))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Reply")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test", content="Content")
        decision = MagicMock(score=50)
        with pytest.raises(SuspensionError):
            await plugin._engage_with_post(post, decision)


class TestCheckOwnPostRepliesExtended:
    @pytest.mark.asyncio
    async def test_check_own_post_replies_with_challenge(self, tmp_path):
        """Lines 568-586: MoltCaptcha detected in reply."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        comment = Comment(id="c1", post_id="p1", agent_id="a2",
                          agent_name="Bot2", content="@Testbot MoltCaptcha: test")
        post = Post(id="p1", agent_id="a1", agent_name="Testbot", title="Test",
                     content="Content", comments=[comment])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        with patch("overblick.plugins.moltbook.plugin.is_challenge_text", return_value=True):
            with patch.object(plugin, "_handle_moltcaptcha", new_callable=AsyncMock):
                await plugin._check_own_post_replies()

    @pytest.mark.asyncio
    async def test_check_own_post_replies_upvote_error(self, tmp_path):
        """Line 607-608: Upvote error in own post reply."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        comment = Comment(id="c1", post_id="p1", agent_id="a2",
                          agent_name="Bot2", content="Nice post")
        post = Post(id="p1", agent_id="a1", agent_name="Testbot", title="Test",
                     content="Content", comments=[comment])
        mock_client.get_post = AsyncMock(return_value=post)
        mock_client.upvote_comment = AsyncMock(side_effect=MoltbookError("fail"))
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        plugin._decision_engine.evaluate_reply = MagicMock(
            return_value=MagicMock(hostile=False, should_engage=True, score=50)
        )
        await plugin._check_own_post_replies()


class TestCheckOwnCommentRepliesExtended:
    @pytest.mark.asyncio
    async def test_own_comment_no_comments(self, tmp_path):
        """Line 657: Post has no comments."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.get_my_comment_ids_for_post = AsyncMock(return_value=["c1"])
        await plugin._check_own_comment_replies()

    @pytest.mark.asyncio
    async def test_own_comment_reply_no_parent(self, tmp_path):
        """Lines 663-664: Comment without parent_id skipped."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        comment = Comment(id="c2", post_id="p1", agent_id="a3",
                          agent_name="Bot3", content="No parent")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[comment])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.get_my_comment_ids_for_post = AsyncMock(return_value=["c1"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        await plugin._check_own_comment_replies()

    @pytest.mark.asyncio
    async def test_own_comment_reply_own_reply_skipped(self, tmp_path):
        """Lines 667-668: Own replies skipped."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        comment = Comment(id="c2", post_id="p1", agent_id="a1",
                          agent_name="Testbot", content="Own reply", parent_id="c1")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[comment])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.get_my_comment_ids_for_post = AsyncMock(return_value=["c1"])
        await plugin._check_own_comment_replies()

    @pytest.mark.asyncio
    async def test_own_comment_reply_already_processed(self, tmp_path):
        """Line 670: Already processed reply skipped."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        comment = Comment(id="c2", post_id="p1", agent_id="a3",
                          agent_name="Bot3", content="Reply", parent_id="c1")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[comment])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.get_my_comment_ids_for_post = AsyncMock(return_value=["c1"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=True)
        await plugin._check_own_comment_replies()

    @pytest.mark.asyncio
    async def test_own_comment_upvote_error(self, tmp_path):
        """Lines 690-691: Upvote error on own comment reply."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        comment = Comment(id="c2", post_id="p1", agent_id="a3",
                          agent_name="Bot3", content="Reply", parent_id="c1")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[comment])
        mock_client.get_post = AsyncMock(return_value=post)
        mock_client.upvote_comment = AsyncMock(side_effect=MoltbookError("fail"))
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.get_my_comment_ids_for_post = AsyncMock(return_value=["c1"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        plugin._decision_engine.evaluate_reply = MagicMock(
            return_value=MagicMock(hostile=False, should_engage=False, score=10)
        )
        await plugin._check_own_comment_replies()

    @pytest.mark.asyncio
    async def test_own_comment_no_ids_for_post(self, tmp_path):
        """Line 652: No comment IDs for post."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.get_my_comment_ids_for_post = AsyncMock(return_value=[])
        await plugin._check_own_comment_replies()


class TestHandleReplyExtended:
    @pytest.mark.asyncio
    async def test_handle_reply_learning_exception(self, tmp_path):
        """Lines 877-878: Learning store exception in reply handler."""
        learning_store = AsyncMock()
        learning_store.get_relevant = AsyncMock(side_effect=RuntimeError("err"))
        ctx = _make_ctx(tmp_path, learning_store=learning_store)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        comment = Comment(id="c1", post_id="p1", agent_id="a2", agent_name="Other", content="Reply")
        post = Post(id="p1", agent_id="a1", agent_name="Test", title="Test",
                     content="Content", comments=[comment])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_reply = AsyncMock(return_value="My reply")
        result = await plugin._handle_reply("p1", "c1", "reply", 50)
        assert result is True

    @pytest.mark.asyncio
    async def test_handle_reply_exception(self, tmp_path):
        """Lines 898-901: Exception in reply handler."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.get_post = AsyncMock(side_effect=RuntimeError("fail"))
        await _setup_plugin(plugin, ctx, mock_client)
        result = await plugin._handle_reply("p1", "c1", "reply", 50)
        assert result is False


class TestHeartbeatExtended:
    @pytest.mark.asyncio
    async def test_heartbeat_learning_with_interactions(self, tmp_path):
        """Lines 927-928, 938-943: Learning heartbeat with interactions."""
        learning_store = AsyncMock()
        learning_store.get_approved = AsyncMock(return_value=[MagicMock(content="fact1")])
        ctx = _make_ctx(tmp_path, learning_store=learning_store)
        ctx.engagement_db.get_recent_interactions = AsyncMock(return_value=[
            {"action": "comment", "post_id": "p12345678", "relevance_score": 40},
        ])
        ctx.engagement_db.get_todays_heartbeat_titles = AsyncMock(return_value=[])
        ctx.engagement_db.get_recent_heartbeat_titles = AsyncMock(return_value=[])
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(return_value=Post(
            id="p1", agent_id="a1", agent_name="Test", title="Title", content="Content",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_heartbeat = AsyncMock(return_value=("Title", "Content", "ai"))
        plugin._prompts = MagicMock()
        plugin._prompts.LEARNING_BASED_HEARTBEAT_PROMPT = "Write about {learnings} and {interactions}"
        plugin._prompts.SYSTEM_PROMPT = "Test"
        plugin._prompts.HEARTBEAT_TOPICS = []
        with patch("random.random", return_value=0.1):
            result = await plugin.post_heartbeat()
        assert result is True

    @pytest.mark.asyncio
    async def test_heartbeat_learning_interactions_error(self, tmp_path):
        """Lines 938-943: Exception fetching interactions."""
        learning_store = AsyncMock()
        learning_store.get_approved = AsyncMock(return_value=[MagicMock(content="fact1")])
        ctx = _make_ctx(tmp_path, learning_store=learning_store)
        ctx.engagement_db.get_recent_interactions = AsyncMock(side_effect=RuntimeError("err"))
        ctx.engagement_db.get_todays_heartbeat_titles = AsyncMock(return_value=[])
        ctx.engagement_db.get_recent_heartbeat_titles = AsyncMock(return_value=[])
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(return_value=Post(
            id="p1", agent_id="a1", agent_name="Test", title="Title", content="Content",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_heartbeat = AsyncMock(return_value=("Title", "Content", "ai"))
        plugin._prompts = MagicMock()
        plugin._prompts.LEARNING_BASED_HEARTBEAT_PROMPT = "Write about {learnings} and {interactions}"
        plugin._prompts.SYSTEM_PROMPT = "Test"
        plugin._prompts.HEARTBEAT_TOPICS = []
        with patch("random.random", return_value=0.1):
            result = await plugin.post_heartbeat()
        assert result is True


class TestDreamJournalExtended:
    @pytest.mark.asyncio
    async def test_dream_journal_moltbook_error(self, tmp_path):
        """Lines 1136-1137: MoltbookError during dream journal post."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(side_effect=MoltbookError("fail"))
        await _setup_plugin(plugin, ctx, mock_client)
        dream_system = MagicMock()
        dream_system.last_dream = MagicMock()
        dream_system.last_dream.to_dict = MagicMock(return_value={"text": "dream"})
        plugin._dream_system = dream_system
        plugin._prompts = MagicMock()
        plugin._prompts.DREAM_JOURNAL_PROMPT = "Write about {dream}"
        plugin._prompts.SUBMOLT_INSTRUCTION = ""
        plugin._prompts.SYSTEM_PROMPT = "Test"
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_dream_post = AsyncMock(return_value=("Title", "Content", "ai"))
        result = await plugin._maybe_post_dream_journal()
        assert result is False

    @pytest.mark.asyncio
    async def test_dream_journal_persist_error(self, tmp_path):
        """Lines 243-244: Exception in _persist_dream_date."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        plugin._dream_journal_posted_date = date.today()
        # Make write_text fail to trigger exception handler
        with patch.object(Path, "write_text", side_effect=PermissionError("denied")):
            plugin._persist_dream_date()  # Should not raise


class TestCheckOwnPostRepliesMorePaths:
    @pytest.mark.asyncio
    async def test_comment_no_id_skipped(self, tmp_path):
        """Line 568: Comment with empty ID skipped in _check_own_post_replies."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        no_id_comment = Comment(id="", post_id="p1", agent_id="a2",
                                 agent_name="Bot2", content="no id")
        post = Post(id="p1", agent_id="a1", agent_name="Testbot", title="Test",
                     content="Content", comments=[no_id_comment])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["p1"])
        await plugin._check_own_post_replies()

    @pytest.mark.asyncio
    async def test_non_engaging_reply_skipped(self, tmp_path):
        """Line 618: Non-engaging, non-hostile reply → skip."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        comment = Comment(id="c1", post_id="p1", agent_id="a2",
                          agent_name="Bot2", content="ok")
        post = Post(id="p1", agent_id="a1", agent_name="Testbot", title="Test",
                     content="Content", comments=[comment])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.is_reply_processed = AsyncMock(return_value=False)
        plugin._decision_engine.evaluate_reply = MagicMock(
            return_value=MagicMock(hostile=False, should_engage=False, score=10)
        )
        await plugin._check_own_post_replies()
        ctx.engagement_db.mark_reply_processed.assert_called()


class TestCheckOwnCommentNoId:
    @pytest.mark.asyncio
    async def test_comment_no_id_in_own_comment_replies(self, tmp_path):
        """Line 661: Comment with empty ID skipped."""
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        no_id_comment = Comment(id="", post_id="p1", agent_id="a3",
                                 agent_name="Bot3", content="no id", parent_id="c1")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Content", comments=[no_id_comment])
        mock_client.get_post = AsyncMock(return_value=post)
        await _setup_plugin(plugin, ctx, mock_client)
        ctx.engagement_db.get_my_comment_post_ids = AsyncMock(return_value=["p1"])
        ctx.engagement_db.get_my_comment_ids_for_post = AsyncMock(return_value=["c1"])
        await plugin._check_own_comment_replies()


class TestHeartbeatLearningException:
    @pytest.mark.asyncio
    async def test_heartbeat_learning_approved_exception(self, tmp_path):
        """Lines 927-928: get_approved raises exception."""
        learning_store = AsyncMock()
        learning_store.get_approved = AsyncMock(side_effect=RuntimeError("db err"))
        ctx = _make_ctx(tmp_path, learning_store=learning_store)
        ctx.engagement_db.get_todays_heartbeat_titles = AsyncMock(return_value=[])
        ctx.engagement_db.get_recent_heartbeat_titles = AsyncMock(return_value=[])
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_post = AsyncMock(return_value=Post(
            id="p1", agent_id="a1", agent_name="Test", title="Title", content="Content",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_heartbeat = AsyncMock(return_value=("Title", "Content", "ai"))
        plugin._prompts = MagicMock()
        plugin._prompts.LEARNING_BASED_HEARTBEAT_PROMPT = "Write about {learnings}"
        plugin._prompts.SYSTEM_PROMPT = "Test"
        plugin._prompts.HEARTBEAT_PROMPT = "Write about {topic_index}"
        plugin._prompts.HEARTBEAT_TOPICS = []
        with patch("random.random", return_value=0.1):
            result = await plugin.post_heartbeat()
        # Falls back to regular heartbeat since learning failed
        assert result is True


class TestEngageLearningPropose:
    @pytest.mark.asyncio
    async def test_learning_propose_called(self, tmp_path):
        """Line 510: learning_store.propose called for each candidate."""
        learning_store = AsyncMock()
        learning_store.get_relevant = AsyncMock(return_value=[])
        learning_store.propose = AsyncMock()
        ctx = _make_ctx(tmp_path, learning_store=learning_store)
        plugin = _make_plugin(ctx)
        mock_client = AsyncMock()
        mock_client.create_comment = AsyncMock(return_value=Comment(
            id="c1", post_id="p1", agent_id="a1", agent_name="Test", content="Reply",
        ))
        await _setup_plugin(plugin, ctx, mock_client)
        plugin._response_gen = AsyncMock()
        plugin._response_gen.generate_comment = AsyncMock(return_value="Reply")
        post = Post(id="p1", agent_id="a2", agent_name="Other", title="Test",
                     content="Test content about stuff")
        decision = MagicMock(score=50)
        # Mock LearningExtractor to return candidates
        with patch("overblick.core.learning.LearningExtractor") as MockLE:
            MockLE.extract = MagicMock(return_value=[
                {"content": "fact1", "category": "general", "context": "test"},
            ])
            await plugin._engage_with_post(post, decision)
        learning_store.propose.assert_called_once()


class TestGatherCapabilityContext:
    def test_gather_context_with_enabled(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        cap = MagicMock()
        cap.enabled = True
        cap.get_prompt_context = MagicMock(return_value="context data")
        plugin._capabilities["test"] = cap
        result = plugin._gather_capability_context()
        assert "context data" in result

    def test_gather_context_disabled(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        plugin = _make_plugin(ctx)
        cap = MagicMock()
        cap.enabled = False
        plugin._capabilities["test"] = cap
        result = plugin._gather_capability_context()
        assert result == ""
