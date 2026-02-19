"""
Coverage gap tests — exercises edge cases and alternate paths
that the main capability tests do not reach.

Focus areas (ordered by coverage gap size):
  - monitoring/inspector.py      (69% → targeting 80%+)
  - psychology/therapy_system.py (78% → targeting 90%+)
  - knowledge/safe_learning.py   (87% → targeting 95%+)
  - knowledge/loader.py          (81% → targeting 95%+)
  - knowledge/learning.py        (79% → targeting 95%+)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ctx(**overrides):
    """Minimal CapabilityContext."""
    from overblick.core.capability import CapabilityContext
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "llm_client": None,
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


# ===========================================================================
# monitoring/inspector.py
# ===========================================================================

class TestRunCommandEdgeCases:
    """Test _run_command exception branches."""

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        """TimeoutError during command execution returns empty string."""
        from overblick.capabilities.monitoring.inspector import _run_command

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
            proc.kill = MagicMock()
            mock_exec.return_value = proc

            result = await _run_command("uptime")
            assert result == ""

    @pytest.mark.asyncio
    async def test_file_not_found_returns_empty(self):
        """FileNotFoundError returns empty string (command not installed)."""
        from overblick.capabilities.monitoring.inspector import _run_command

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await _run_command("uptime")
            assert result == ""

    @pytest.mark.asyncio
    async def test_generic_exception_returns_empty(self):
        """Any unexpected exception returns empty string."""
        from overblick.capabilities.monitoring.inspector import _run_command

        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("unexpected")):
            result = await _run_command("uptime")
            assert result == ""

    @pytest.mark.asyncio
    async def test_nonzero_returncode_returns_empty(self):
        """Non-zero exit code from command returns empty string."""
        from overblick.capabilities.monitoring.inspector import _run_command

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.returncode = 1
            proc.communicate = AsyncMock(return_value=(b"output", b"error"))
            mock_exec.return_value = proc

            result = await _run_command("df", "-h")
            assert result == ""


class TestInspectorExceptionPaths:
    """Test inspect() when individual collectors fail."""

    @pytest.mark.asyncio
    async def test_inspect_with_collector_exceptions(self):
        """inspect() handles exceptions from individual collectors gracefully."""
        from overblick.capabilities.monitoring.inspector import HostInspectionCapability
        from overblick.capabilities.monitoring.models import HostHealth

        inspector = HostInspectionCapability()

        async def _raising_collector():
            raise RuntimeError("collector failed")

        with patch.object(inspector, "_collect_memory", side_effect=RuntimeError("mem fail")), \
             patch.object(inspector, "_collect_uptime", new_callable=AsyncMock, return_value="1 day"), \
             patch.object(inspector, "_collect_cpu", new_callable=AsyncMock) as mock_cpu:
            from overblick.capabilities.monitoring.models import CPUInfo
            mock_cpu.return_value = CPUInfo(load_1m=0.5, core_count=4)

            health = await inspector.inspect()

            assert isinstance(health, HostHealth)
            # Errors are recorded for failed collectors
            assert any("mem fail" in e for e in health.errors)


class TestLinuxMemoryCollection:
    """Test Linux-specific memory collection path."""

    @pytest.mark.asyncio
    async def test_collect_memory_linux_proc_meminfo(self):
        """Linux /proc/meminfo parsing produces correct MemoryInfo."""
        from overblick.capabilities.monitoring.inspector import HostInspectionCapability

        inspector = HostInspectionCapability()
        inspector._platform = "linux"  # Force Linux path

        proc_meminfo = (
            "MemTotal:       16777216 kB\n"
            "MemFree:         2097152 kB\n"
            "MemAvailable:    8388608 kB\n"
            "Buffers:          524288 kB\n"
        )

        async def _mock_cmd(*args):
            if args and args[0] == "cat":
                return proc_meminfo
            return ""

        with patch("overblick.capabilities.monitoring.inspector._run_command",
                   side_effect=_mock_cmd):
            mem = await inspector._collect_memory_linux()

            assert mem.total_mb == pytest.approx(16384.0, rel=0.01)
            assert mem.available_mb == pytest.approx(8192.0, rel=0.01)
            assert mem.percent_used > 0

    @pytest.mark.asyncio
    async def test_collect_memory_linux_falls_back_to_free(self):
        """Linux memory falls back to 'free' command when cat fails."""
        from overblick.capabilities.monitoring.inspector import HostInspectionCapability

        inspector = HostInspectionCapability()
        inspector._platform = "linux"

        free_output = (
            "              total        used        free      shared  buff/cache   available\n"
            "Mem:           8192        4000        2000         100        2192        4000\n"
        )

        async def _mock_cmd(*args):
            if args and args[0] == "cat":
                return ""  # cat /proc/meminfo fails
            if args and args[0] == "free":
                return free_output
            return ""

        with patch("overblick.capabilities.monitoring.inspector._run_command",
                   side_effect=_mock_cmd):
            mem = await inspector._collect_memory_linux()
            assert mem.total_mb == 8192.0
            assert mem.used_mb == 4000.0

    @pytest.mark.asyncio
    async def test_collect_memory_linux_all_fail(self):
        """When all Linux memory commands fail, returns default MemoryInfo."""
        from overblick.capabilities.monitoring.inspector import HostInspectionCapability
        from overblick.capabilities.monitoring.models import MemoryInfo

        inspector = HostInspectionCapability()
        inspector._platform = "linux"

        with patch("overblick.capabilities.monitoring.inspector._run_command",
                   new_callable=AsyncMock, return_value=""):
            mem = await inspector._collect_memory_linux()
            assert isinstance(mem, MemoryInfo)
            assert mem.total_mb == 0


class TestInspectorParsing:
    """Test static parsing helpers."""

    def test_parse_size_to_gb_terabyte(self):
        from overblick.capabilities.monitoring.inspector import HostInspectionCapability
        inspector = HostInspectionCapability()
        assert inspector._parse_size_to_gb("2T") == 2048.0

    def test_parse_size_to_gb_mebibyte(self):
        from overblick.capabilities.monitoring.inspector import HostInspectionCapability
        inspector = HostInspectionCapability()
        result = inspector._parse_size_to_gb("1024M")
        assert abs(result - 1.0) < 0.01

    def test_parse_size_to_gb_bytes_no_suffix(self):
        from overblick.capabilities.monitoring.inspector import HostInspectionCapability
        inspector = HostInspectionCapability()
        result = inspector._parse_size_to_gb("1073741824")  # 1 GB in bytes
        assert abs(result - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_uptime_fallback_parsing(self):
        """Uptime parser falls back gracefully for unusual formats."""
        from overblick.capabilities.monitoring.inspector import HostInspectionCapability

        inspector = HostInspectionCapability()

        async def _mock_cmd(*args):
            # Format without "users" — triggers fallback regex
            return "10:00  up 3 days, load averages: 1.20 1.10 1.00"

        with patch("overblick.capabilities.monitoring.inspector._run_command",
                   side_effect=_mock_cmd):
            uptime = await inspector._collect_uptime()
            assert "3 days" in uptime or uptime  # should return something non-empty


# ===========================================================================
# psychology/therapy_system.py
# ===========================================================================

class TestTherapySystemBranches:
    """Tests for therapy system code paths not covered by wrapper tests."""

    def test_session_to_dict_serialization(self):
        """TherapySession.to_dict() returns all expected fields."""
        from overblick.capabilities.psychology.therapy_system import TherapySession

        session = TherapySession(
            week_number=3,
            dreams_processed=2,
            learnings_processed=1,
            dream_themes=["transformation"],
            synthesis_insights=["growth"],
            shadow_patterns=["fear"],
        )
        d = session.to_dict()
        assert d["week_number"] == 3
        assert d["dreams_processed"] == 2
        assert d["dream_themes"] == ["transformation"]
        assert d["synthesis_insights"] == ["growth"]
        assert d["shadow_patterns"] == ["fear"]
        assert "timestamp" in d

    @pytest.mark.asyncio
    async def test_run_session_with_dreams_and_synthesis(self):
        """run_session with dreams + synthesis_prompt triggers synthesis path."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value={"content": "- Insight one\n- Insight two"})

        ts = TherapySystem(llm_client=mock_llm, system_prompt="You are a therapist.")
        dreams = [{"dream_type": "shadow", "content": "I was running from something dark"}]

        session = await ts.run_session(
            dreams=dreams,
            synthesis_prompt="Synthesize: {dream_themes}, {learning_count}, {dream_count}",
        )
        assert session.week_number == 1
        assert session.dreams_processed == 1
        # Synthesis should have populated insights (LLM was called)
        assert isinstance(session.synthesis_insights, list)

    @pytest.mark.asyncio
    async def test_run_session_with_post_prompt(self):
        """run_session with post_prompt + LLM generates post title/content."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value={
            "content": "Reflections on Darkness\n\nThis week I walked through shadows..."
        })

        ts = TherapySystem(llm_client=mock_llm, system_prompt="Therapist prompt.")
        dreams = [{"dream_type": "anima", "content": "A figure appeared in mist"}]

        session = await ts.run_session(
            dreams=dreams,
            post_prompt=(
                "Week {week_number}, {dreams_processed} dreams, "
                "{learnings_processed} learnings, themes: {dream_themes}, "
                "shadow: {shadow_patterns}, insights: {synthesis_insights}"
            ),
        )
        assert session.post_title is not None
        assert session.post_content is not None

    @pytest.mark.asyncio
    async def test_analyze_themes_no_llm(self):
        """_analyze_themes returns [] immediately when no LLM."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem

        ts = TherapySystem(llm_client=None)
        items = [{"dream_type": "shadow", "content": "dark figure"}]
        result = await ts._analyze_themes(items, "prompt: {items}")
        assert result == []

    @pytest.mark.asyncio
    async def test_analyze_themes_no_prompt(self):
        """_analyze_themes returns [] when prompt template is empty."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem

        mock_llm = AsyncMock()
        ts = TherapySystem(llm_client=mock_llm)
        result = await ts._analyze_themes([{"content": "x"}], "")
        assert result == []

    @pytest.mark.asyncio
    async def test_analyze_themes_llm_exception(self):
        """_analyze_themes returns [] on LLM exception (graceful degradation)."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        ts = TherapySystem(llm_client=mock_llm)

        result = await ts._analyze_themes(
            [{"dream_type": "x", "content": "test"}], "Analyze: {items}"
        )
        assert result == []

    def test_extract_shadow_patterns(self):
        """_extract_shadow_patterns finds shadow keywords in dreams."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem

        ts = TherapySystem()
        dreams = [
            {"content": "A dark shadow followed me", "insight": "I felt fear"},
            {"content": "Hidden corridor in a mansion", "insight": ""},
        ]
        patterns = ts._extract_shadow_patterns(dreams)
        assert "dark" in patterns or "shadow" in patterns
        assert "fear" in patterns or "hidden" in patterns

    def test_extract_archetypes(self):
        """_extract_archetypes identifies archetype keywords."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem

        ts = TherapySystem()
        dreams = [
            {"content": "An old sage gave me wisdom on a quest"},
            {"content": "A trickster led me astray"},
        ]
        archetypes = ts._extract_archetypes(dreams)
        assert "wise old man" in archetypes or "hero" in archetypes
        assert "trickster" in archetypes

    @pytest.mark.asyncio
    async def test_synthesize_no_llm(self):
        """_synthesize returns [] when no LLM client."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem

        ts = TherapySystem(llm_client=None)
        result = await ts._synthesize([], [], [], "template: {dream_themes} {learning_count} {dream_count}")
        assert result == []

    @pytest.mark.asyncio
    async def test_synthesize_llm_exception(self):
        """_synthesize returns [] on LLM exception."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=ConnectionError("offline"))
        ts = TherapySystem(llm_client=mock_llm)

        result = await ts._synthesize(
            [], [], ["theme1"],
            "Themes: {dream_themes}, learnings: {learning_count}, dreams: {dream_count}",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_generate_post_exception_returns_none(self):
        """_generate_post returns (None, None, 'ai') on LLM exception."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem, TherapySession

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("generation failed"))
        ts = TherapySystem(llm_client=mock_llm, system_prompt="prompt")

        session = TherapySession(week_number=1)
        title, content, submolt = await ts._generate_post(session, "Post: {week_number} {dreams_processed} {learnings_processed} {dream_themes} {shadow_patterns} {synthesis_insights}")
        assert title is None
        assert content is None
        assert submolt == "ai"

    def test_generate_summary_quiet_week(self):
        """_generate_summary describes a quiet week with no material."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem, TherapySession

        ts = TherapySystem()
        session = TherapySession(week_number=2, dreams_processed=0, learnings_processed=0)
        summary = ts._generate_summary(session)
        assert "Week 2" in summary

    def test_generate_summary_with_insights(self):
        """_generate_summary includes insight count when present."""
        from overblick.capabilities.psychology.therapy_system import TherapySystem, TherapySession

        ts = TherapySystem()
        session = TherapySession(
            week_number=5,
            dreams_processed=3,
            learnings_processed=2,
            synthesis_insights=["Growth", "Acceptance"],
        )
        summary = ts._generate_summary(session)
        assert "5" in summary
        assert "3" in summary or "dreams" in summary.lower()


# ===========================================================================
# knowledge/safe_learning.py
# ===========================================================================

class TestSafeLearningEdgeCases:
    """Tests for safe_learning.py code paths not covered elsewhere."""

    def test_proposed_learning_to_dict(self):
        """ProposedLearning.to_dict() returns expected structure."""
        from overblick.capabilities.knowledge.safe_learning import (
            ProposedLearning, LearningCategory, ReviewResult,
        )
        pl = ProposedLearning(
            category=LearningCategory.FACTUAL,
            content="The sky is blue",
            source_context="chat conversation",
            source_agent="TestBot",
        )
        d = pl.to_dict()
        assert d["category"] == "factual"
        assert d["content"] == "The sky is blue"
        assert d["source_agent"] == "TestBot"
        assert d["review_result"] == "pending"
        assert d["stored"] is False

    @pytest.mark.asyncio
    async def test_review_learning_refine_response(self):
        """REFINE response sets NEEDS_REFINEMENT status."""
        from overblick.capabilities.knowledge.safe_learning import (
            SafeLearningModule, LearningCategory, ReviewResult,
        )

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value={"content": "REFINE: Be more specific"})

        module = SafeLearningModule(llm_client=mock_llm, ethos_text="Be ethical")
        learning = module.propose_learning(
            "AI is smart", LearningCategory.OPINION, "test", "Bot"
        )
        result = await module.review_learning(learning)
        assert result == ReviewResult.NEEDS_REFINEMENT
        assert learning.review_result == ReviewResult.NEEDS_REFINEMENT
        # Still in pending (not moved to approved/rejected)
        assert learning in module.pending_learnings

    @pytest.mark.asyncio
    async def test_review_learning_exception_rejects(self):
        """LLM exception during review results in REJECTED."""
        from overblick.capabilities.knowledge.safe_learning import (
            SafeLearningModule, LearningCategory, ReviewResult,
        )

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

        module = SafeLearningModule(llm_client=mock_llm)
        learning = module.propose_learning(
            "Some fact", LearningCategory.FACTUAL, "ctx", "Bot"
        )
        result = await module.review_learning(learning)
        assert result == ReviewResult.REJECTED
        assert "Review error" in learning.review_reason

    @pytest.mark.asyncio
    async def test_review_all_pending_counts_needs_refinement(self):
        """review_all_pending() correctly counts NEEDS_REFINEMENT results."""
        from overblick.capabilities.knowledge.safe_learning import (
            SafeLearningModule, LearningCategory, ReviewResult,
        )

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value={"content": "REFINE: Needs more detail"})

        module = SafeLearningModule(llm_client=mock_llm)
        module.propose_learning("Fact 1", LearningCategory.FACTUAL, "ctx", "Bot")
        module.propose_learning("Fact 2", LearningCategory.FACTUAL, "ctx", "Bot")

        counts = await module.review_all_pending()
        assert counts["needs_refinement"] == 2
        assert counts["approved"] == 0
        assert counts["rejected"] == 0

    @pytest.mark.asyncio
    async def test_review_learning_no_llm_returns_pending(self):
        """Without LLM client, review returns PENDING."""
        from overblick.capabilities.knowledge.safe_learning import (
            SafeLearningModule, LearningCategory, ReviewResult,
        )
        module = SafeLearningModule(llm_client=None)
        learning = module.propose_learning("Test", LearningCategory.FACTUAL, "ctx", "Bot")
        result = await module.review_learning(learning)
        assert result == ReviewResult.PENDING


# ===========================================================================
# knowledge/loader.py (KnowledgeCapability) — uninitialised paths
# ===========================================================================

class TestKnowledgeCapabilityUninitialised:
    """Test all fallback paths when KnowledgeCapability is not set up."""

    @pytest.mark.asyncio
    async def test_get_knowledge_no_loader(self):
        """get_knowledge() returns [] when no loader."""
        from overblick.capabilities.knowledge.loader import KnowledgeCapability
        ctx = make_ctx(config={"knowledge_dir": "/tmp/nonexistent_xyz_999"})
        cap = KnowledgeCapability(ctx)
        await cap.setup()
        assert cap.get_knowledge() == []

    @pytest.mark.asyncio
    async def test_get_knowledge_by_category_no_loader(self):
        """get_knowledge(category=...) returns [] when no loader."""
        from overblick.capabilities.knowledge.loader import KnowledgeCapability
        ctx = make_ctx(config={"knowledge_dir": "/tmp/nonexistent_xyz_999"})
        cap = KnowledgeCapability(ctx)
        await cap.setup()
        assert cap.get_knowledge(category="tech") == []

    @pytest.mark.asyncio
    async def test_categories_no_loader(self):
        """categories property returns [] when no loader."""
        from overblick.capabilities.knowledge.loader import KnowledgeCapability
        ctx = make_ctx(config={"knowledge_dir": "/tmp/nonexistent_xyz_999"})
        cap = KnowledgeCapability(ctx)
        await cap.setup()
        assert cap.categories == []

    def test_inner_is_none_without_setup(self):
        """inner property is None before setup()."""
        from overblick.capabilities.knowledge.loader import KnowledgeCapability
        ctx = make_ctx()
        cap = KnowledgeCapability(ctx)
        assert cap.inner is None


# ===========================================================================
# knowledge/learning.py (LearningCapability) — uninitialised paths
# ===========================================================================

class TestLearningCapabilityUninitialised:
    """Test all fallback paths when LearningCapability.setup() not called."""

    def test_propose_learning_no_module_returns_none(self):
        """propose_learning() returns None before setup()."""
        from overblick.capabilities.knowledge.learning import LearningCapability
        from overblick.capabilities.knowledge.safe_learning import LearningCategory
        ctx = make_ctx()
        cap = LearningCapability(ctx)
        # No setup() called → _module is None
        result = cap.propose_learning("Test", LearningCategory.FACTUAL, "ctx", "Bot")
        assert result is None

    @pytest.mark.asyncio
    async def test_review_all_pending_no_module_returns_zero_counts(self):
        """review_all_pending() returns zero counts before setup()."""
        from overblick.capabilities.knowledge.learning import LearningCapability
        ctx = make_ctx()
        cap = LearningCapability(ctx)
        counts = await cap.review_all_pending()
        assert counts == {"approved": 0, "rejected": 0, "needs_refinement": 0}

    def test_pending_learnings_no_module_returns_empty(self):
        """pending_learnings returns [] before setup()."""
        from overblick.capabilities.knowledge.learning import LearningCapability
        ctx = make_ctx()
        cap = LearningCapability(ctx)
        assert cap.pending_learnings == []

    def test_approved_learnings_no_module_returns_empty(self):
        """approved_learnings returns [] before setup()."""
        from overblick.capabilities.knowledge.learning import LearningCapability
        ctx = make_ctx()
        cap = LearningCapability(ctx)
        assert cap.approved_learnings == []

    def test_inner_is_none_without_setup(self):
        """inner property is None before setup()."""
        from overblick.capabilities.knowledge.learning import LearningCapability
        ctx = make_ctx()
        cap = LearningCapability(ctx)
        assert cap.inner is None
