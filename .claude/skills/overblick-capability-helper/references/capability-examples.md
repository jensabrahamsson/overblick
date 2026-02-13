# Capability Examples — Real Patterns from Överblick

## ConversationCapability — Multi-Turn Tracking

**File:** `overblick/capabilities/conversation/tracker.py`
**Registry name:** `conversation_tracker`

Reusable conversation tracking extracted from TelegramPlugin's inline pattern. Shows how to make a capability that's useful across multiple plugins.

```python
class ConversationCapability(CapabilityBase):
    name = "conversation_tracker"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._conversations: dict[str, ConversationEntry] = {}
        self._max_history: int = 10
        self._stale_seconds: int = 3600

    async def setup(self) -> None:
        # Read config from identity YAML
        self._max_history = self.ctx.config.get("max_history", 10)
        self._stale_seconds = self.ctx.config.get("stale_seconds", 3600)

    def get_or_create(self, conversation_id: str) -> ConversationEntry:
        """Get or create a conversation entry."""
        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = ConversationEntry(
                conversation_id=conversation_id,
                max_history=self._max_history,
            )
        return self._conversations[conversation_id]

    def add_user_message(self, conversation_id: str, text: str) -> None:
        entry = self.get_or_create(conversation_id)
        entry.add_user_message(text)

    def add_assistant_message(self, conversation_id: str, text: str) -> None:
        entry = self.get_or_create(conversation_id)
        entry.add_assistant_message(text)

    def get_messages(self, conversation_id: str, system_prompt: str = "") -> list[dict]:
        entry = self._conversations.get(conversation_id)
        if not entry:
            if system_prompt:
                return [{"role": "system", "content": system_prompt}]
            return []
        return entry.get_messages(system_prompt)

    async def tick(self) -> None:
        """Periodic cleanup of stale conversations."""
        self.cleanup_stale()

    def cleanup_stale(self) -> int:
        stale = [cid for cid, entry in self._conversations.items()
                 if (time.time() - entry.last_active) > self._stale_seconds]
        for cid in stale:
            del self._conversations[cid]
        return len(stale)
```

**Key pattern:** Exposes domain-specific methods (add_user_message, get_messages) while handling lifecycle (cleanup in tick).

## AnalyzerCapability — DecisionEngine Wrapper

**File:** `overblick/capabilities/engagement/analyzer.py`
**Registry name:** `analyzer`

Wraps the DecisionEngine module as a composable capability. Shows the pattern of wrapping existing modules.

```python
class AnalyzerCapability(CapabilityBase):
    name = "analyzer"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._engine: Optional[DecisionEngine] = None

    async def setup(self) -> None:
        # Config from identity YAML passed through ctx.config
        interest_keywords = self.ctx.config.get("interest_keywords", [])
        engagement_threshold = self.ctx.config.get("engagement_threshold", 35.0)
        fuzzy_threshold = self.ctx.config.get("fuzzy_threshold", 75)
        self_agent_name = self.ctx.config.get("agent_name", self.ctx.identity_name)

        self._engine = DecisionEngine(
            interest_keywords=interest_keywords,
            engagement_threshold=engagement_threshold,
            fuzzy_threshold=fuzzy_threshold,
            self_agent_name=self_agent_name,
        )

    def evaluate(self, title, content, agent_name, submolt="") -> EngagementDecision:
        if not self._engine:
            return EngagementDecision(should_engage=False, score=0.0, action="skip", reason="not initialized")
        return self._engine.evaluate_post(title, content, agent_name, submolt)

    @property
    def inner(self) -> Optional[DecisionEngine]:
        """Access underlying engine for tests/migration."""
        return self._engine
```

**Key pattern:** `inner` property provides escape hatch for gradual migration.

## SummarizerCapability — LLM-Using Capability

**File:** `overblick/capabilities/content/summarizer.py`
**Registry name:** `summarizer`

Shows how a capability uses the LLM pipeline.

```python
class SummarizerCapability(CapabilityBase):
    name = "summarizer"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._max_length: int = 200

    async def setup(self) -> None:
        self._max_length = self.ctx.config.get("max_summary_length", 200)

    async def summarize(self, text: str, context: str = "") -> str:
        """Summarize text using LLM pipeline."""
        if not self.ctx.llm_pipeline:
            # Fallback: simple truncation
            return text[:self._max_length] + "..." if len(text) > self._max_length else text

        messages = [
            {"role": "system", "content": f"Summarize the following in under {self._max_length} characters."},
            {"role": "user", "content": text},
        ]

        result = await self.ctx.llm_pipeline.chat(
            messages=messages,
            audit_action="summarize",
            audit_details={"input_length": len(text)},
        )

        if result.blocked:
            return text[:self._max_length]
        return result.content or text[:self._max_length]
```

**Key pattern:** Graceful fallback when LLM is unavailable. Always handle `result.blocked`.

## DreamCapability — Tick-Based with Quiet Hours

**File:** `overblick/capabilities/psychology/dream.py`
**Registry name:** `dream_system`

Generates "morning dreams" during quiet hours. Shows tick-based pattern with scheduling awareness.

```python
class DreamCapability(CapabilityBase):
    name = "dream_system"

    def __init__(self, ctx: CapabilityContext):
        super().__init__(ctx)
        self._dream_log: list[str] = []
        self._last_dream_date: Optional[str] = None

    async def setup(self) -> None:
        self._templates = self.ctx.config.get("dream_templates")

    async def tick(self) -> None:
        """Generate dream during quiet hours (once per day)."""
        if not self.ctx.quiet_hours_checker:
            return
        if not self.ctx.quiet_hours_checker.is_quiet_hours():
            return

        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_dream_date == today:
            return  # Already dreamed today

        dream = await self._generate_dream()
        if dream:
            self._dream_log.append(dream)
            self._last_dream_date = today

    def get_prompt_context(self) -> str:
        """Inject recent dreams into LLM prompts."""
        if not self._dream_log:
            return ""
        recent = self._dream_log[-3:]
        return f"\n[Recent dreams: {'; '.join(recent)}]\n"
```

**Key patterns:**
- Checks quiet hours before acting
- Deduplicates (once per day)
- `get_prompt_context()` feeds back into LLM prompts

## Test Patterns

### Basic Capability Test with `make_ctx` Helper

```python
from overblick.core.capability import CapabilityContext


def make_ctx(tmp_path, **overrides):
    """Create a test CapabilityContext."""
    defaults = {
        "identity_name": "test",
        "data_dir": tmp_path,
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestConversationCapability:
    def test_creation(self, tmp_path):
        ctx = make_ctx(tmp_path)
        cap = ConversationCapability(ctx)
        assert cap.name == "conversation_tracker"
        assert cap.enabled is True

    @pytest.mark.asyncio
    async def test_setup_with_config(self, tmp_path):
        ctx = make_ctx(tmp_path, config={"max_history": 20, "stale_seconds": 7200})
        cap = ConversationCapability(ctx)
        await cap.setup()
        assert cap._max_history == 20

    @pytest.mark.asyncio
    async def test_tick_cleans_stale(self, tmp_path):
        ctx = make_ctx(tmp_path, config={"stale_seconds": 0})
        cap = ConversationCapability(ctx)
        await cap.setup()
        cap.add_user_message("conv1", "hello")
        await cap.tick()  # Should clean up immediately (stale_seconds=0)
        assert cap.active_count == 0
```

### Testing from_plugin_context

```python
def test_from_plugin_context(self, tmp_path):
    plugin_ctx = PluginContext(
        identity_name="anomal",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_client="mock_llm",
        event_bus="mock_bus",
    )
    cap_ctx = CapabilityContext.from_plugin_context(plugin_ctx, config={"key": "val"})
    assert cap_ctx.identity_name == "anomal"
    assert cap_ctx.llm_client == "mock_llm"
    assert cap_ctx.config == {"key": "val"}
```

### Testing Capability with LLM Pipeline

```python
@pytest.mark.asyncio
async def test_summarize_with_pipeline(self, tmp_path):
    pipeline = AsyncMock()
    pipeline.chat = AsyncMock(return_value=PipelineResult(content="Short summary"))

    ctx = make_ctx(tmp_path, llm_pipeline=pipeline)
    cap = SummarizerCapability(ctx)
    await cap.setup()

    result = await cap.summarize("Very long text " * 100)
    assert result == "Short summary"
    pipeline.chat.assert_called_once()

@pytest.mark.asyncio
async def test_summarize_fallback_without_pipeline(self, tmp_path):
    ctx = make_ctx(tmp_path)  # No pipeline
    cap = SummarizerCapability(ctx)
    await cap.setup()

    result = await cap.summarize("Short text")
    assert result == "Short text"  # Returns input unchanged
```
