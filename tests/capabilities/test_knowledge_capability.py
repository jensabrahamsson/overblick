"""Tests for KnowledgeCapability and KnowledgeLoader coverage gaps.

KnowledgeLoader uncovered: 45-46, 52, 65
KnowledgeCapability (loader.py) uncovered: 53, 59, 66
"""

from pathlib import Path

import pytest

from overblick.capabilities.knowledge.knowledge_loader import KnowledgeLoader
from overblick.capabilities.knowledge.loader import KnowledgeCapability
from overblick.core.capability import CapabilityContext


def make_ctx(**overrides) -> CapabilityContext:
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestKnowledgeLoaderCoverage:
    def test_load_error_handled_gracefully(self, tmp_path):
        """Cover lines 45-46: exception during YAML load."""
        kf = tmp_path / "knowledge_broken.yaml"
        kf.write_text("{{{{invalid yaml")
        loader = KnowledgeLoader(tmp_path)
        assert loader.total_items == 0

    def test_get_knowledge_all_categories(self, tmp_path):
        """Cover line 52: get_knowledge with no category returns all items."""
        kf = tmp_path / "knowledge_test.yaml"
        kf.write_text("cat1:\n  - item1\ncat2:\n  - item2\n  - item3")
        loader = KnowledgeLoader(tmp_path)
        all_items = loader.get_knowledge()
        assert len(all_items) == 3

    def test_format_for_prompt_exceeds_max_items(self, tmp_path):
        """Cover line 65: max_items limit reached during formatting."""
        kf = tmp_path / "knowledge_test.yaml"
        items = "\n".join(f"  - item{i}" for i in range(25))
        kf.write_text(f"big_category:\n{items}")
        loader = KnowledgeLoader(tmp_path)
        formatted = loader.format_for_prompt(max_items=5)
        assert "KNOWLEDGE BASE" in formatted
        # Count the items in the output
        assert formatted.count("  - item") == 5

    def test_string_value_in_yaml(self, tmp_path):
        """Cover line 41: items is a string, not a list."""
        kf = tmp_path / "knowledge_test.yaml"
        kf.write_text("single_fact: This is a single fact string")
        loader = KnowledgeLoader(tmp_path)
        items = loader.get_knowledge("single_fact")
        assert items == ["This is a single fact string"]


class TestKnowledgeCapabilityCoverage:
    @pytest.mark.asyncio
    async def test_get_prompt_context_not_initialized(self):
        """Cover line 53 in loader.py: no loader returns empty string."""
        ctx = make_ctx(config={"knowledge_dir": "/nonexistent/path"})
        cap = KnowledgeCapability(ctx)
        await cap.setup()
        assert cap.get_prompt_context() == ""

    @pytest.mark.asyncio
    async def test_get_knowledge_not_initialized(self):
        """Cover line 59 in loader.py: no loader returns empty list."""
        ctx = make_ctx(config={"knowledge_dir": "/nonexistent/path"})
        cap = KnowledgeCapability(ctx)
        await cap.setup()
        assert cap.get_knowledge() == []

    @pytest.mark.asyncio
    async def test_categories_not_initialized(self):
        """Cover line 66 in loader.py: no loader returns empty list."""
        ctx = make_ctx(config={"knowledge_dir": "/nonexistent/path"})
        cap = KnowledgeCapability(ctx)
        await cap.setup()
        assert cap.categories == []

    @pytest.mark.asyncio
    async def test_setup_with_custom_knowledge_dir(self, tmp_path):
        """Setup with custom knowledge_dir that exists."""
        kf = tmp_path / "knowledge_test.yaml"
        kf.write_text("facts:\n  - fact1\n  - fact2")

        ctx = make_ctx(config={"knowledge_dir": str(tmp_path)})
        cap = KnowledgeCapability(ctx)
        await cap.setup()

        assert cap.inner is not None
        assert cap.inner.total_items == 2
        assert cap.categories == ["facts"]

    @pytest.mark.asyncio
    async def test_setup_default_path(self):
        """Setup with default path (identities/<name>/)."""
        ctx = make_ctx(identity_name="anomal")
        cap = KnowledgeCapability(ctx)
        await cap.setup()
        # Anomal has knowledge files — should load
        assert cap.inner is not None

    @pytest.mark.asyncio
    async def test_get_prompt_context_with_loader(self, tmp_path):
        """Cover line 53: get_prompt_context when loader exists."""
        kf = tmp_path / "knowledge_test.yaml"
        kf.write_text("facts:\n  - important fact")

        ctx = make_ctx(config={"knowledge_dir": str(tmp_path)})
        cap = KnowledgeCapability(ctx)
        await cap.setup()

        result = cap.get_prompt_context()
        assert "KNOWLEDGE BASE" in result
        assert "important fact" in result

    @pytest.mark.asyncio
    async def test_get_knowledge_with_loader(self, tmp_path):
        """Cover line 59: get_knowledge when loader exists."""
        kf = tmp_path / "knowledge_test.yaml"
        kf.write_text("facts:\n  - fact1\n  - fact2")

        ctx = make_ctx(config={"knowledge_dir": str(tmp_path)})
        cap = KnowledgeCapability(ctx)
        await cap.setup()

        result = cap.get_knowledge("facts")
        assert len(result) == 2
