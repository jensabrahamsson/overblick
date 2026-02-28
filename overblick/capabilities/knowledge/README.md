# Knowledge Capabilities

## Overview

The **knowledge** bundle provides knowledge loading, management, and safe learning capabilities for agent plugins. It enables agents to load identity-specific knowledge from YAML files, inject it into LLM prompts, and acquire new knowledge through an LLM-powered ethical review process.

This bundle is the foundation of agent expertise — it defines what an agent knows and how they learn new information.

## Capabilities

### KnowledgeCapability

Wraps the KnowledgeLoader module to load identity-specific `knowledge_*.yaml` files and format them for LLM prompt injection. Knowledge files are organized by category (facts, concepts, procedures, etc.) and can be filtered and sampled for context injection.

**Registry name:** `knowledge_loader`

### LearningCapability ⚠️ DEPRECATED

> **Deprecated.** The `safe_learning` capability and its underlying `SafeLearningModule` are replaced by the **platform learning system** at `overblick/core/learning/`. The new system provides per-identity SQLite persistence, immediate ethos review at propose time, and embedding-based semantic retrieval. See [`overblick/core/learning/README.md`](../../core/learning/README.md) for full documentation.
>
> **Migration:** Replace `ctx.capabilities.get("safe_learning")` with `ctx.learning_store` (injected by the orchestrator into every PluginContext). The `safe_learning` capability remains registered for backward compatibility but should not be used in new code.

Wraps the SafeLearningModule to enable LLM-reviewed knowledge acquisition with ethical review gates.

**Registry name:** `safe_learning`

## Methods

### KnowledgeCapability

```python
def get_prompt_context(self, max_items: int = 10) -> str:
    """
    Return formatted knowledge for injection into LLM prompts.

    Samples up to max_items knowledge entries across all categories
    and formats them as a structured prompt section.
    """

def get_knowledge(self, category: Optional[str] = None) -> list[str]:
    """
    Get knowledge items, optionally filtered by category.

    Args:
        category: Optional category filter (e.g. "facts", "concepts").

    Returns:
        List of knowledge item strings.
    """

@property
def categories(self) -> list[str]:
    """Get all knowledge categories available."""

@property
def inner(self) -> Optional[KnowledgeLoader]:
    """Access the underlying KnowledgeLoader (for tests/migration)."""
```

Configuration options (set in identity YAML under `capabilities.knowledge_loader`):
- `knowledge_dir` (Path, optional) — Override default knowledge directory

### LearningCapability

```python
def propose_learning(
    self,
    content: str,
    category: LearningCategory,
    source_context: str,
    source_agent: str,
) -> Optional[ProposedLearning]:
    """
    Propose a new learning for review.

    Args:
        content: The knowledge content to learn.
        category: Learning category (FACTUAL, OPINION, PERSON, PATTERN, CORRECTION).
        source_context: Context where this learning originated.
        source_agent: Agent who provided this information.

    Returns:
        ProposedLearning instance or None if invalid.
    """

async def review_all_pending(self) -> dict:
    """
    Review all pending learnings using LLM ethical review.

    Returns:
        {"approved": int, "rejected": int, "needs_refinement": int}
    """

@staticmethod
def extract_potential_learnings(
    conversation: str,
    response: str,
    agent_name: str,
) -> list[dict]:
    """
    Extract potential learnings from a conversation.

    Returns:
        List of dicts with keys: content, category, source_context.
    """

@property
def pending_learnings(self) -> list:
    """Pending learnings awaiting review."""

@property
def approved_learnings(self) -> list:
    """Approved learnings (ready to be saved to knowledge files)."""

@property
def inner(self) -> Optional[SafeLearningModule]:
    """Access the underlying SafeLearningModule (for tests/migration)."""
```

Configuration options (set in identity YAML under `capabilities.safe_learning`):
- `ethos_text` (str, optional) — Agent's ethical framework for learning review

## Plugin Integration

Plugins access knowledge capabilities through the CapabilityContext:

```python
from overblick.core.capability import CapabilityRegistry

class MoltbookPlugin(PluginBase):
    async def setup(self) -> None:
        registry = CapabilityRegistry.default()

        # Load knowledge bundle (knowledge_loader + safe_learning)
        caps = registry.create_all(["knowledge"], self.ctx, configs={
            "knowledge_loader": {
                "knowledge_dir": self.data_dir / "knowledge",
            },
            "safe_learning": {
                "ethos_text": self.identity.ethos,
            },
        })
        for cap in caps:
            await cap.setup()

        self.knowledge = caps[0]
        self.learning = caps[1]

    async def generate_response(self, prompt: str) -> str:
        # Inject knowledge into prompt
        knowledge_context = self.knowledge.get_prompt_context(max_items=15)

        full_prompt = f"""
{self.system_prompt}

{knowledge_context}

User: {prompt}
Assistant:"""

        response = await self.llm_client.chat(messages=[
            {"role": "user", "content": full_prompt}
        ])

        # Extract learnings from interaction
        learnings = self.learning.extract_potential_learnings(
            conversation=prompt,
            response=response["content"],
            agent_name=self.identity.name,
        )

        # Propose learnings for review
        for item in learnings:
            self.learning.propose_learning(
                content=item["content"],
                category=item["category"],
                source_context=item["source_context"],
                source_agent="conversation",
            )

        # Periodically review pending learnings
        if len(self.learning.pending_learnings) >= 10:
            await self.learning.review_all_pending()

        return response["content"]
```

## Configuration

Configure the knowledge bundle in your personality's `personality.yaml`:

```yaml
capabilities:
  knowledge_loader:
    knowledge_dir: data/cherry/knowledge  # Optional override

  safe_learning:
    ethos_text: |
      I value truth, curiosity, and ethical reasoning.
      I avoid learning unverified claims, harmful techniques, or biased information.
      I prioritize learning that expands understanding and helps others.
```

Or load the entire bundle:

```yaml
capabilities:
  - knowledge  # Expands to: knowledge_loader, safe_learning
```

## Usage Examples

### Loading Knowledge from YAML

Create knowledge files in `data/<identity>/knowledge_*.yaml`:

```yaml
# data/cherry/knowledge_ai.yaml
facts:
  - Neural networks use backpropagation to learn from data
  - Transformers use self-attention mechanisms
  - GPT models are decoder-only transformers

concepts:
  - Emergence: complex behavior arising from simple rules
  - Alignment: ensuring AI systems act according to human values
  - Interpretability: understanding how neural networks make decisions

procedures:
  - "To train a neural network: 1) Initialize weights 2) Forward pass 3) Calculate loss 4) Backpropagate 5) Update weights"
```

```python
from overblick.capabilities.knowledge import KnowledgeCapability
from overblick.core.capability import CapabilityContext

# Initialize capability
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    config={},
)

knowledge = KnowledgeCapability(ctx)
await knowledge.setup()

# Get formatted knowledge for prompt injection
context = knowledge.get_prompt_context(max_items=10)
print(context)
# Output:
# === Cherry's Knowledge ===
#
# Facts:
# - Neural networks use backpropagation to learn from data
# - Transformers use self-attention mechanisms
#
# Concepts:
# - Emergence: complex behavior arising from simple rules
# - Alignment: ensuring AI systems act according to human values
#
# Procedures:
# - To train a neural network: 1) Initialize weights...

# Get knowledge by category
facts = knowledge.get_knowledge(category="facts")
print(f"Facts: {facts}")

# List all categories
categories = knowledge.categories
print(f"Categories: {categories}")
```

### Proposing New Learnings

```python
from overblick.capabilities.knowledge import LearningCapability, LearningCategory

# Initialize learning capability
ctx = CapabilityContext(
    identity_name="cherry",
    data_dir=Path("data/cherry"),
    llm_client=ollama_client,
    config={
        "ethos_text": "I value truth, curiosity, and ethical reasoning.",
    },
)

learning = LearningCapability(ctx)
await learning.setup()

# Propose a new fact
learning.propose_learning(
    content="GPT-4 was released in March 2023",
    category=LearningCategory.FACTUAL,
    source_context="Discussion with Alice about AI history",
    source_agent="alice",
)

# Propose an opinion
learning.propose_learning(
    content="Attention is all you need: attention mechanisms can replace recurrence",
    category=LearningCategory.OPINION,
    source_context="Reading research papers",
    source_agent="research",
)

# Check pending learnings
print(f"Pending: {len(learning.pending_learnings)}")
```

### LLM-Powered Ethical Review

```python
# Review pending learnings against ethos
results = await learning.review_all_pending()

print(f"Approved: {results['approved']}")
print(f"Rejected: {results['rejected']}")
print(f"Needs refinement: {results['needs_refinement']}")

# Access approved learnings
for item in learning.approved_learnings:
    print(f"✓ {item.content} ({item.category})")
    # Save to knowledge file:
    # knowledge.add_to_file(item.content, item.category)
```

### Extract Learnings from Conversations

```python
# Extract potential learnings from a conversation
conversation = "What's the capital of France?"
response = "The capital of France is Paris, a major European city known for art and culture."

learnings = LearningCapability.extract_potential_learnings(
    conversation=conversation,
    response=response,
    agent_name="cherry",
)

for item in learnings:
    print(f"Extracted: {item['content']} (category: {item['category']})")
    # Output: "The capital of France is Paris" (category: FACTUAL)
```

### Combining Knowledge and Learning

```python
# 1. Load existing knowledge
knowledge_context = knowledge.get_prompt_context(max_items=15)

# 2. Use in LLM prompt
prompt = f"""
{system_prompt}

{knowledge_context}

User: Tell me about transformers in AI.
Assistant:"""

response = await llm_client.chat(messages=[{"role": "user", "content": prompt}])

# 3. Extract new learnings from response
potential_learnings = LearningCapability.extract_potential_learnings(
    conversation="Tell me about transformers in AI.",
    response=response["content"],
    agent_name="cherry",
)

# 4. Propose for review
for item in potential_learnings:
    learning.propose_learning(
        content=item["content"],
        category=item["category"],
        source_context="AI conversation",
        source_agent="user",
    )

# 5. Review and approve
if len(learning.pending_learnings) >= 5:
    await learning.review_all_pending()

# 6. Save approved learnings to knowledge files
# (Plugin responsibility — not automatic)
```

## Testing

Run knowledge capability tests:

```bash
# Test knowledge loader (no LLM required)
pytest tests/capabilities/test_capabilities.py::test_knowledge_capability -v

# Test safe learning (requires LLM)
pytest tests/capabilities/test_capabilities.py::test_learning_capability -v -m llm
```

Example test patterns:

```python
import pytest
from overblick.capabilities.knowledge import KnowledgeCapability, LearningCapability
from overblick.core.capability import CapabilityContext

@pytest.mark.asyncio
async def test_knowledge_loading(tmp_path):
    # Create test knowledge file
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "knowledge_test.yaml").write_text("""
facts:
  - Test fact 1
  - Test fact 2

concepts:
  - Test concept 1
""")

    ctx = CapabilityContext(
        identity_name="test",
        data_dir=tmp_path,
        config={"knowledge_dir": knowledge_dir},
    )

    knowledge = KnowledgeCapability(ctx)
    await knowledge.setup()

    # Get knowledge
    facts = knowledge.get_knowledge(category="facts")
    assert len(facts) == 2
    assert "Test fact 1" in facts

    # Get prompt context
    context = knowledge.get_prompt_context(max_items=10)
    assert "Test fact 1" in context
    assert "Test concept 1" in context

@pytest.mark.asyncio
async def test_safe_learning(mock_llm_client):
    ctx = CapabilityContext(
        identity_name="test",
        data_dir=Path("/tmp"),
        llm_client=mock_llm_client,
        config={"ethos_text": "I value truth and ethics."},
    )

    learning = LearningCapability(ctx)
    await learning.setup()

    # Propose learning
    learning.propose_learning(
        content="New AI breakthrough",
        category=LearningCategory.FACTUAL,
        source_context="test",
        source_agent="test",
    )

    assert len(learning.pending_learnings) == 1

    # Review
    results = await learning.review_all_pending()
    assert results["approved"] + results["rejected"] == 1
```

## Architecture

### KnowledgeLoader (Internal Module)

The KnowledgeCapability wraps the KnowledgeLoader module:

```python
class KnowledgeLoader:
    def __init__(self, knowledge_dir: Path):
        self._knowledge: dict[str, list[str]] = {}
        self._load_all_knowledge_files(knowledge_dir)

    def get_knowledge(self, category: Optional[str] = None) -> list[str]
    def format_for_prompt(self, max_items: int = 10) -> str
    @property
    def categories(self) -> list[str]
    @property
    def total_items(self) -> int
```

Knowledge files are YAML dictionaries with category keys:

```yaml
category_name:
  - Item 1
  - Item 2
  - Item 3
```

### SafeLearningModule (Internal Module)

The LearningCapability wraps the SafeLearningModule:

```python
class LearningCategory(Enum):
    FACTUAL = "factual"
    OPINION = "opinion"
    PERSON = "person"
    PATTERN = "pattern"
    CORRECTION = "correction"

class ProposedLearning(BaseModel):
    content: str
    category: LearningCategory
    source_context: str
    source_agent: str
    timestamp: float

class ReviewResult(Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REFINEMENT = "needs_refinement"

class SafeLearningModule:
    def __init__(self, llm_client, ethos_text: str = ""):
        self._llm = llm_client
        self._ethos = ethos_text
        self._pending: list[ProposedLearning] = []
        self._approved: list[ProposedLearning] = []

    def propose_learning(...) -> Optional[ProposedLearning]
    async def review_all_pending(self) -> dict
```

### Ethical Review Process

When `review_all_pending()` is called, each proposed learning is evaluated:

1. **LLM Review Prompt:**
   ```
   You are an ethical learning reviewer. Evaluate this proposed learning against
   the agent's ethos:

   Ethos: {ethos_text}

   Proposed Learning:
   - Content: {content}
   - Category: {category}
   - Source: {source_agent} ({source_context})

   Should this learning be accepted? Consider:
   - Factual accuracy (if applicable)
   - Alignment with ethos
   - Potential harm or bias
   - Usefulness and relevance

   Respond with: APPROVED, REJECTED, or NEEDS_REFINEMENT
   Reason: <brief explanation>
   ```

2. **Decision:** Based on LLM response, learning is approved, rejected, or marked for refinement.

3. **Storage:** Approved learnings are stored in `_approved` list. Plugins can then save them to knowledge files.

### Prompt Context Injection

Knowledge is formatted for LLM prompts:

```python
def format_for_prompt(self, max_items: int = 10) -> str:
    """
    Format knowledge for LLM prompt injection.

    Samples up to max_items across categories, formats as:

    === {Identity}'s Knowledge ===

    Category1:
    - Item 1
    - Item 2

    Category2:
    - Item 3
    """
```

This context is prepended to system prompts to ground the agent's responses in their knowledge base.

## Platform Learning System (Replacement)

The `safe_learning` capability is superseded by the **platform learning system** (`overblick/core/learning/`). Key differences:

| Aspect | Old (SafeLearningModule) | New (LearningStore) |
|--------|--------------------------|---------------------|
| Scope | Per-plugin (in-memory) | Per-identity (SQLite) |
| Review | Batch (`review_all_pending`) | Immediate at propose time |
| Storage | In-memory lists | SQLite with persistence |
| Retrieval | All approved (no ranking) | Embedding-based similarity |
| Integration | Via capability registry | Via `PluginContext.learning_store` |

Plugins should use `ctx.learning_store` for all new learning integration. See [`overblick/core/learning/README.md`](../../core/learning/README.md).

## Related Bundles

- **engagement** — Use knowledge in response generation
- **psychology** — Combine knowledge with emotional/dream context
- **content** — Summarize knowledge before injection
