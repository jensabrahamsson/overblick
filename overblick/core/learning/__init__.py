"""
Platform-level learning system for Överblick.

Provides per-identity learning with ethos validation, embedding-based
retrieval, and SQLite persistence. Accessible via PluginContext.

Usage:
    store = LearningStore(db_path, ethos_text, llm_pipeline, embed_fn)
    await store.setup()

    # Propose (immediately reviewed against ethos)
    learning = await store.propose("Attachment theory ...", category="factual", source="moltbook")

    # Retrieve relevant learnings for context injection
    learnings = await store.get_relevant("How do attachment styles work?")
"""

from .extractor import LearningExtractor
from .models import Learning, LearningStatus
from .store import LearningStore

__all__ = ["Learning", "LearningExtractor", "LearningStatus", "LearningStore"]
