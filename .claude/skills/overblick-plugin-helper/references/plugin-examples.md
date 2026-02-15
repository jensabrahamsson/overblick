# Plugin Examples — Real Patterns from Överblick

## TelegramPlugin (Condensed)

**File:** `overblick/plugins/telegram/plugin.py`

A conversational bot that receives messages, routes through the personality-driven LLM pipeline, and responds in character.

### Setup Pattern

```python
class TelegramPlugin(PluginBase):
    name = "telegram"

    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self._bot_token: Optional[str] = None
        self._conversations: dict[int, ConversationContext] = {}
        self._user_rate_limits: dict[int, UserRateLimit] = {}
        self._system_prompt: str = ""
        self._messages_received = 0

    async def setup(self) -> None:
        identity = self.ctx.identity

        # Load secret — fail fast if missing
        self._bot_token = self.ctx.get_secret("telegram_bot_token")
        if not self._bot_token:
            raise RuntimeError(f"Missing telegram_bot_token for {identity.name}")

        # Build system prompt from personality
        self._system_prompt = self._build_system_prompt(identity)

        # Load config from identity
        raw_config = identity.raw_config
        allowed = raw_config.get("telegram", {}).get("allowed_chat_ids", [])
        self._allowed_chat_ids = set(allowed)

        # Audit
        self.ctx.audit_log.log(
            action="plugin_setup",
            details={"plugin": self.name, "identity": identity.name},
        )
```

### Message Handling with Security

```python
async def _handle_message(self, chat_id, user_id, username, text, message_id):
    # 1. Wrap user input in boundary markers
    safe_text = wrap_external_content(text, "telegram_message")

    # 2. Use shared conversation tracker if available, else local
    shared_caps = getattr(self.ctx, "capabilities", {}) or {}
    tracker = shared_caps.get("conversation_tracker")

    if tracker:
        tracker.add_user_message(str(chat_id), safe_text)
        messages = tracker.get_messages(str(chat_id), self._system_prompt)
    else:
        conv = self._get_conversation(chat_id)
        conv.add_user_message(safe_text, username)
        messages = conv.get_messages(self._system_prompt)

    # 3. Generate response via SafeLLMPipeline (not raw client)
    result = await self.ctx.llm_pipeline.chat(
        messages=messages,
        user_id=str(user_id),
        audit_action="telegram_response",
        audit_details={"chat_id": chat_id, "username": username},
    )

    # 4. Handle blocked responses
    if result.blocked:
        if result.deflection:
            await self._send_message(chat_id, result.deflection, reply_to=message_id)
        else:
            await self._send_message(chat_id, "I can't respond to that.", reply_to=message_id)
        return

    response = result.content or ""
```

### Rate Limiting Pattern

```python
class UserRateLimit(BaseModel):
    user_id: int
    message_timestamps: list[float] = []
    max_per_minute: int = 10
    max_per_hour: int = 60

    def is_allowed(self) -> bool:
        now = time.time()
        self.message_timestamps = [t for t in self.message_timestamps if now - t < 3600]
        per_minute = sum(1 for t in self.message_timestamps if now - t < 60)
        return per_minute < self.max_per_minute and len(self.message_timestamps) < self.max_per_hour
```

## MoltbookPlugin — Capability Integration Pattern

**File:** `overblick/plugins/moltbook/plugin.py`

The most complex plugin — shows full capability integration.

### Loading Capabilities

```python
async def _setup_capabilities(self, enabled_modules: list[str], system_prompt: str):
    """Load capabilities from enabled_modules list."""
    # 1. Use shared capabilities from orchestrator first
    shared = getattr(self.ctx, "capabilities", {}) or {}
    if shared:
        for name, cap in shared.items():
            if name not in self._capabilities:
                self._capabilities[name] = cap

    # 2. Create remaining capabilities locally
    registry = CapabilityRegistry.default()
    identity = self.ctx.identity

    # Per-capability configs from identity YAML
    configs = {
        "dream_system": {"dream_templates": identity.raw_config.get("dream_templates")},
        "therapy_system": {"therapy_day": identity.raw_config.get("therapy_day", 6)},
        "safe_learning": {"ethos_text": identity.raw_config.get("ethos_text", "")},
        "emotional_state": {},
    }

    # Resolve bundles (e.g., "psychology" -> [dream, therapy, emotional])
    resolved = registry.resolve(enabled_modules)
    for name in resolved:
        if name in self._capabilities:
            continue
        cap = registry.create(name, self.ctx, config=configs.get(name, {}))
        if cap:
            await cap.setup()
            self._capabilities[cap.name] = cap
```

### Gathering Capability Context for Prompts

```python
def _gather_capability_context(self) -> str:
    """Collect prompt context from all enabled capabilities."""
    parts = []
    for cap in self._capabilities.values():
        if cap.enabled:
            ctx = cap.get_prompt_context()
            if ctx:
                parts.append(ctx)
    return "".join(parts)
```

### Capability Teardown

```python
async def teardown(self) -> None:
    # Teardown capabilities in reverse order
    for cap in reversed(list(self._capabilities.values())):
        try:
            await cap.teardown()
        except Exception as e:
            logger.warning("Capability teardown error (%s): %s", cap.name, e)

    if self._client:
        await self._client.close()
```

## Test Fixture Patterns

### Shared Fixtures (`tests/conftest.py`)

```python
@pytest.fixture
def mock_llm_client():
    """Mock LLM client."""
    client = AsyncMock()
    client.chat = AsyncMock(return_value={"content": "Test response"})
    client.health_check = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client

@pytest.fixture
def mock_audit_log():
    """Mock audit log."""
    log = MagicMock()
    log.log = MagicMock()
    log.query = MagicMock(return_value=[])
    return log

@pytest.fixture
def mock_engagement_db():
    """Mock engagement database."""
    db = MagicMock()
    db.record_engagement = MagicMock()
    db.track_my_post = MagicMock()
    db.track_my_comment = MagicMock()
    db.get_my_post_ids = MagicMock(return_value=[])
    db.is_reply_processed = MagicMock(return_value=False)
    return db

@pytest.fixture
def mock_quiet_hours_checker():
    checker = MagicMock()
    checker.is_quiet_hours = MagicMock(return_value=False)
    return checker
```

### Plugin-Specific Fixtures (`tests/plugins/<name>/conftest.py`)

```python
@pytest.fixture
def telegram_identity():
    identity = MagicMock(spec=Identity)
    identity.name = "test"
    identity.raw_config = {
        "telegram": {
            "allowed_chat_ids": [12345],
            "rate_limit_per_minute": 10,
        }
    }
    identity.llm = MagicMock()
    identity.llm.temperature = 0.7
    identity.llm.max_tokens = 500
    return identity

@pytest.fixture
def telegram_context(telegram_identity, tmp_path, mock_llm_client, mock_audit_log):
    pipeline = AsyncMock(spec=SafeLLMPipeline)
    pipeline.chat = AsyncMock(return_value=PipelineResult(content="Test response"))

    ctx = PluginContext(
        identity_name="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_client=mock_llm_client,
        llm_pipeline=pipeline,
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=telegram_identity,
    )
    ctx._secrets_getter = lambda k: {"telegram_bot_token": "test-token"}.get(k)
    return ctx

@pytest.fixture
def telegram_plugin(telegram_context):
    return TelegramPlugin(telegram_context)
```

### Test Patterns

```python
class TestTelegramSetup:
    @pytest.mark.asyncio
    async def test_setup_loads_token(self, telegram_plugin):
        await telegram_plugin.setup()
        assert telegram_plugin._bot_token == "test-token"

    @pytest.mark.asyncio
    async def test_setup_fails_without_token(self, telegram_context):
        telegram_context._secrets_getter = lambda k: None
        plugin = TelegramPlugin(telegram_context)
        with pytest.raises(RuntimeError, match="Missing telegram_bot_token"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_setup_logs_audit(self, telegram_plugin, mock_audit_log):
        await telegram_plugin.setup()
        mock_audit_log.log.assert_called()

class TestTelegramTick:
    @pytest.mark.asyncio
    async def test_tick_skips_quiet_hours(self, telegram_plugin):
        await telegram_plugin.setup()
        telegram_plugin.ctx.quiet_hours_checker.is_quiet_hours.return_value = True
        await telegram_plugin.tick()
        # No processing should happen

    @pytest.mark.asyncio
    async def test_pipeline_blocked_response(self, telegram_plugin):
        await telegram_plugin.setup()
        telegram_plugin.ctx.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                blocked=True,
                block_reason="Unsafe content",
                deflection="I can't help with that.",
            )
        )
        # Verify blocked handling...
```
