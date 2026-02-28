# Platform Learning System

## Overview

The **platform learning system** (`overblick/core/learning/`) provides per-identity knowledge acquisition with ethos-gated validation and embedding-based semantic retrieval. Every identity has ONE shared learning store — all plugins for that identity contribute to and draw from the same store.

This is a **core platform service**, not a capability. The orchestrator initializes it per-identity and injects it into every plugin via `PluginContext.learning_store`.

## Design Principles

- **Per identity, not per plugin** — Cherry has ONE learning store shared by Moltbook, email, IRC, etc.
- **Ethos gate** — every proposed learning is LLM-reviewed against the identity's ethos values before approval
- **Embedding-based retrieval** — approved learnings are embedded; injected by semantic relevance to current context
- **Direct review** — ethos review happens immediately at propose time (synchronous in tick)
- **Graceful degradation** — works without embeddings (recency fallback), without LLM (stays as candidate), without learning_store (backward compat via None check)
- **Fail-safe** — LLM failure returns CANDIDATE status, not APPROVED or REJECTED

## Architecture

```
overblick/core/learning/
├── __init__.py          # Re-exports: LearningStore, Learning, LearningStatus, LearningExtractor
├── store.py             # LearningStore — SQLite persistence + embedding retrieval
├── reviewer.py          # EthosReviewer — LLM-based validation
├── extractor.py         # LearningExtractor — extract candidates from text
├── models.py            # Learning, LearningStatus
└── migrations.py        # SQLite schema
```

### Data Flow

```
Text (post, comment, reflection)
    → LearningExtractor.extract()          # Pattern-based candidate extraction
    → LearningStore.propose()
        → EthosReviewer.review()           # LLM validates against identity ethos
        → embed_fn(content)                # Compute embedding (if available)
        → SQLite INSERT                    # Persist with status + embedding
    → LearningStore.get_relevant(context)  # Cosine similarity search
        → Injected into LLM prompt         # Decorates personality with learned knowledge
```

## Data Model

```python
class LearningStatus(str, Enum):
    CANDIDATE = "candidate"    # Proposed, review failed or pending
    APPROVED = "approved"      # Passed ethos review
    REJECTED = "rejected"      # Failed ethos review

class Learning(BaseModel):
    id: Optional[int] = None
    content: str                          # The learned insight
    category: str = "general"             # factual, social, opinion, pattern, correction
    source: str = ""                      # "moltbook", "email", "reflection", "irc"
    source_context: str = ""              # What triggered the learning (max 500 chars)
    status: LearningStatus = LearningStatus.CANDIDATE
    review_reason: str = ""               # Why approved/rejected
    confidence: float = 0.5               # 0.0–1.0
    embedding: Optional[list[float]] = None  # Vector for similarity search
    created_at: str = ""
    reviewed_at: Optional[str] = None
```

## API

### LearningStore

```python
class LearningStore:
    def __init__(
        self,
        db_path: Path,          # data/<identity>/learnings.db
        ethos_text: str,        # Identity's ethos for review
        llm_pipeline=None,      # SafeLLMPipeline for ethos review
        embed_fn=None,          # async callable(text) -> list[float]
    ): ...

    async def setup(self) -> None:
        """Run migrations and ensure the database is ready."""

    async def propose(
        self, content: str, category: str = "general",
        source: str = "", source_context: str = "",
    ) -> Learning:
        """Propose a learning. Immediately reviewed against ethos.
        If approved AND embed_fn available, compute embedding.
        Returns the Learning with final status."""

    async def get_relevant(self, context: str, limit: int = 8) -> list[Learning]:
        """Get approved learnings most relevant to context.
        Uses cosine similarity if embeddings available.
        Falls back to most recent approved learnings otherwise."""

    async def get_approved(self, limit: int = 10) -> list[Learning]:
        """Get latest approved learnings ordered by recency."""

    async def count(self, status: Optional[LearningStatus] = None) -> int:
        """Count learnings, optionally filtered by status."""
```

### LearningExtractor

```python
class LearningExtractor:
    @staticmethod
    def extract(text: str, source_agent: str = "") -> list[dict]:
        """Extract learning candidates from text using pattern matching.
        Looks for teaching indicators: 'did you know', 'actually',
        'research shows', 'studies show', etc.
        Returns list of dicts with keys: content, category, context."""
```

### EthosReviewer

```python
class EthosReviewer:
    def __init__(self, llm_pipeline, ethos_text: str): ...

    async def review(self, content: str, category: str) -> tuple[LearningStatus, str]:
        """Single LLM call to review learning against ethos.
        Returns (status, reason). On LLM failure: (CANDIDATE, reason)."""
```

## Plugin Usage

Plugins access the learning store via `self.ctx.learning_store`:

```python
class MoltbookPlugin(PluginBase):
    async def _engage_with_post(self, post):
        # Extract learnings from post content
        if self.ctx.learning_store:
            candidates = LearningExtractor.extract(post.content, source_agent=post.agent_name)
            for c in candidates:
                await self.ctx.learning_store.propose(
                    content=c["content"],
                    category=c["category"],
                    source="moltbook",
                    source_context=c["context"],
                )

        # Inject relevant learnings into response context
        if self.ctx.learning_store:
            learnings = await self.ctx.learning_store.get_relevant(
                context=post.content, limit=8,
            )
            extra_context = "\n".join(f"- {l.content}" for l in learnings)
```

## Orchestrator Integration

The orchestrator initializes ONE LearningStore per identity and passes it to all plugins:

```python
# In Orchestrator._setup_learning_store():
ethos = identity.raw_config.get("ethos", [])
ethos_text = "\n".join(ethos) if isinstance(ethos, list) else str(ethos)

learning_store = LearningStore(
    db_path=data_dir / "learnings.db",
    ethos_text=ethos_text,
    llm_pipeline=self._llm_pipeline,
    embed_fn=self._get_embed_fn(),  # From OllamaClient/GatewayClient embed()
)
await learning_store.setup()

# Passed to every PluginContext for this identity
ctx = PluginContext(..., learning_store=learning_store)
```

## Embedding Infrastructure

Embeddings are generated via Ollama's `/api/embed` endpoint (default model: `nomic-embed-text`):

- **OllamaClient.embed(text)** — Direct Ollama call
- **GatewayClient.embed(text)** — Via LLM Gateway `/v1/embeddings`
- **Gateway endpoint** — `POST /v1/embeddings?text=...&model=nomic-embed-text`

Embeddings are stored as packed float32 BLOBs in SQLite (`struct.pack('f' * N, *floats)`). Cosine similarity search is done in pure Python — with <10k learnings per identity, brute-force is fast enough.

**Graceful degradation:** If no embedding model is available or `embed_fn` is None, `get_relevant()` falls back to `get_approved()` (most recent learnings). The system works without embeddings, just less precisely.

## Agentic Integration

The agentic loop (OBSERVE/THINK/PLAN/ACT/REFLECT) routes through LearningStore when available:

- **ReflectionPipeline._store_learnings()** — Routes through `LearningStore.propose()` instead of `AgenticDB.add_learning()`
- **AgentLoop THINK step** — Reads from `LearningStore.get_relevant()` instead of `AgenticDB.get_learnings()`
- **Backward compat** — If `learning_store` is None, falls back to `AgenticDB` methods

## Relationship to Legacy System

The `capabilities/knowledge/` bundle still exists but the `safe_learning` capability is **deprecated** in favor of this core learning system:

| Aspect | Old (SafeLearningModule) | New (LearningStore) |
|--------|--------------------------|---------------------|
| Scope | Per-plugin (in-memory) | Per-identity (SQLite) |
| Review | Batch (`review_all_pending`) | Immediate at propose time |
| Storage | In-memory lists | SQLite with persistence |
| Retrieval | All approved (no ranking) | Embedding-based similarity |
| Integration | Via capability registry | Via `PluginContext.learning_store` |

The `knowledge_loader` capability (loading static YAML knowledge files) remains unchanged and active.

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS identity_learnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    source TEXT DEFAULT '',
    source_context TEXT DEFAULT '',
    status TEXT DEFAULT 'candidate',
    review_reason TEXT DEFAULT '',
    confidence REAL DEFAULT 0.5,
    embedding BLOB DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    reviewed_at TEXT DEFAULT NULL
);
CREATE INDEX idx_learnings_status ON identity_learnings(status);
```

## Testing

```bash
# Unit tests
pytest tests/core/learning/ -v

# Specific modules
pytest tests/core/learning/test_store.py -v          # LearningStore + cosine + blob roundtrip
pytest tests/core/learning/test_reviewer.py -v       # EthosReviewer
pytest tests/core/learning/test_extractor.py -v      # LearningExtractor
pytest tests/core/learning/test_models.py -v         # Data models
pytest tests/core/learning/test_integration.py -v    # End-to-end with real SQLite

# Agentic integration
pytest tests/core/agentic/test_learning_integration.py -v

# Moltbook engagement (upvotes, hostile detection)
pytest tests/plugins/moltbook/test_engagement.py -v
```
