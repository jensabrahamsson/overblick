"""Tests for knowledge loader."""

import pytest
from pathlib import Path
from overblick.plugins.moltbook.knowledge_loader import KnowledgeLoader


class TestKnowledgeLoader:
    def test_load_from_identity_dir(self):
        # Use the real Anomal identity directory
        identity_dir = Path(__file__).parent.parent.parent.parent / "overblick" / "identities" / "anomal"
        if not identity_dir.exists():
            pytest.skip("Anomal identity dir not found")

        loader = KnowledgeLoader(identity_dir)
        assert loader.total_items > 0
        assert len(loader.categories) > 0

    def test_format_for_prompt(self):
        identity_dir = Path(__file__).parent.parent.parent.parent / "overblick" / "identities" / "anomal"
        if not identity_dir.exists():
            pytest.skip("Anomal identity dir not found")

        loader = KnowledgeLoader(identity_dir)
        formatted = loader.format_for_prompt(max_items=5)
        assert "KNOWLEDGE BASE" in formatted

    def test_empty_directory(self, tmp_path):
        loader = KnowledgeLoader(tmp_path)
        assert loader.total_items == 0
        assert loader.format_for_prompt() == ""

    def test_get_knowledge_by_category(self, tmp_path):
        # Create a test knowledge file
        kf = tmp_path / "knowledge_test.yaml"
        kf.write_text("category1:\n  - fact1\n  - fact2\ncategory2:\n  - fact3")

        loader = KnowledgeLoader(tmp_path)
        assert len(loader.get_knowledge("category1")) == 2
        assert len(loader.get_knowledge("category2")) == 1
        assert len(loader.get_knowledge("nonexistent")) == 0
