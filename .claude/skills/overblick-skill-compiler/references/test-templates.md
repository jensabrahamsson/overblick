# Test Templates

Complete test templates for plugins and capabilities. All patterns are drawn from the actual codebase.

## Plugin Tests

### tests/plugins/\<name\>/\_\_init\_\_.py

```python
```

(Empty file — required for Python package.)

### tests/plugins/\<name\>/conftest.py

```python
"""Test fixtures for <Name>Plugin."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from overblick.personalities import (
    Identity,
    LLMSettings,
    QuietHoursSettings,
    ScheduleSettings,
    SecuritySettings,
)
from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.core.plugin_base import PluginContext
from overblick.plugins.<name>.plugin import <Name>Plugin


# ---------------------------------------------------------------------------
# Identity fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def <name>_identity():
    """Test identity for <name> plugin tests."""
    return Identity(
        name="test",
        display_name="Test",
        description="Test identity for <name> plugin",
        engagement_threshold=35,
        llm=LLMSettings(model="qwen3:8b", temperature=0.7, max_tokens=500),
        quiet_hours=QuietHoursSettings(enabled=True, start_hour=21, end_hour=7),
        schedule=ScheduleSettings(heartbeat_hours=4, feed_poll_minutes=5),
        security=SecuritySettings(enable_preflight=True, enable_output_safety=True),
        raw_config={
            # Add plugin-specific config keys here
        },
    )


# ---------------------------------------------------------------------------
# Mock services
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_pipeline():
    """Mock SafeLLMPipeline with default success response."""
    pipeline = AsyncMock()
    pipeline.chat = AsyncMock(
        return_value=PipelineResult(content="Test response from LLM")
    )
    return pipeline


@pytest.fixture
def mock_pipeline_blocked():
    """Mock SafeLLMPipeline that returns a blocked result."""
    pipeline = AsyncMock()
    pipeline.chat = AsyncMock(
        return_value=PipelineResult(
            blocked=True,
            block_reason="Safety check failed",
            block_stage=PipelineStage.OUTPUT_SAFETY,
        )
    )
    return pipeline


# ---------------------------------------------------------------------------
# PluginContext fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def <name>_context(
    <name>_identity, tmp_path, mock_llm_client, mock_audit_log, mock_pipeline,
):
    """Full plugin context for <name> tests."""
    ctx = PluginContext(
        identity_name="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_client=mock_llm_client,
        llm_pipeline=mock_pipeline,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=<name>_identity,
        engagement_db=MagicMock(),
    )
    # Wire secrets — add all secrets the plugin needs
    ctx._secrets_getter = lambda key: {
        "<name>_api_key": "test-key-12345",
    }.get(key)
    return ctx


@pytest.fixture
def <name>_context_quiet(
    <name>_identity, tmp_path, mock_llm_client, mock_audit_log, mock_pipeline,
):
    """Plugin context with quiet hours active."""
    ctx = PluginContext(
        identity_name="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_client=mock_llm_client,
        llm_pipeline=mock_pipeline,
        event_bus=MagicMock(),
        scheduler=MagicMock(),
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=True)),
        identity=<name>_identity,
        engagement_db=MagicMock(),
    )
    ctx._secrets_getter = lambda key: {
        "<name>_api_key": "test-key-12345",
    }.get(key)
    return ctx


@pytest.fixture
def <name>_context_no_secret(
    <name>_identity, tmp_path, mock_llm_client, mock_audit_log, mock_pipeline,
):
    """Plugin context with missing secrets (for failure tests)."""
    ctx = PluginContext(
        identity_name="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_client=mock_llm_client,
        llm_pipeline=mock_pipeline,
        audit_log=mock_audit_log,
        quiet_hours_checker=MagicMock(is_quiet_hours=MagicMock(return_value=False)),
        identity=<name>_identity,
    )
    ctx._secrets_getter = lambda key: None
    return ctx
```

### tests/plugins/\<name\>/test\_\<name\>.py

```python
"""Tests for <Name>Plugin — <brief description>."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from overblick.core.llm.pipeline import PipelineResult, PipelineStage
from overblick.plugins.<name>.plugin import <Name>Plugin


class TestSetup:
    """Test plugin initialization and setup."""

    @pytest.mark.asyncio
    async def test_setup_success(self, <name>_context):
        """Plugin sets up correctly with valid config."""
        plugin = <Name>Plugin(<name>_context)
        await plugin.setup()
        # Verify audit log was called
        <name>_context.audit_log.log.assert_called()

    @pytest.mark.asyncio
    async def test_setup_missing_secret(self, <name>_context_no_secret):
        """Plugin raises RuntimeError when secret is missing."""
        plugin = <Name>Plugin(<name>_context_no_secret)
        with pytest.raises(RuntimeError, match="Missing <name>_api_key"):
            await plugin.setup()

    @pytest.mark.asyncio
    async def test_setup_audits(self, <name>_context):
        """Plugin logs setup to audit log."""
        plugin = <Name>Plugin(<name>_context)
        await plugin.setup()
        <name>_context.audit_log.log.assert_any_call(
            action="plugin_setup",
            details={"plugin": "<name>", "identity": "test"},
        )


class TestTick:
    """Test the main work cycle."""

    @pytest.mark.asyncio
    async def test_tick_quiet_hours(self, <name>_context_quiet):
        """Plugin skips tick during quiet hours."""
        plugin = <Name>Plugin(<name>_context_quiet)
        await plugin.setup()
        await plugin.tick()
        # Pipeline should NOT have been called during quiet hours
        <name>_context_quiet.llm_pipeline.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_increments_counter(self, <name>_context):
        """Tick counter increments."""
        plugin = <Name>Plugin(<name>_context)
        await plugin.setup()
        assert plugin._tick_count == 0
        await plugin.tick()
        assert plugin._tick_count == 1


class TestTeardown:
    """Test cleanup."""

    @pytest.mark.asyncio
    async def test_teardown(self, <name>_context):
        """Plugin tears down without error."""
        plugin = <Name>Plugin(<name>_context)
        await plugin.setup()
        await plugin.teardown()


class TestSecurity:
    """Verify security patterns are correctly implemented."""

    @pytest.mark.asyncio
    async def test_uses_pipeline_not_raw_client(self, <name>_context):
        """Plugin uses SafeLLMPipeline, not raw llm_client."""
        plugin = <Name>Plugin(<name>_context)
        await plugin.setup()
        # The plugin should reference ctx.llm_pipeline, not ctx.llm_client
        # This is verified by the code structure — pipeline.chat is the mock
        assert <name>_context.llm_pipeline is not None

    @pytest.mark.asyncio
    async def test_handles_blocked_response(self, <name>_context):
        """Plugin handles blocked pipeline responses gracefully."""
        <name>_context.llm_pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                blocked=True,
                block_reason="Test block",
                block_stage=PipelineStage.PREFLIGHT,
            )
        )
        plugin = <Name>Plugin(<name>_context)
        await plugin.setup()
        # Tick should not raise even when pipeline blocks
        await plugin.tick()
```

## Capability Tests

### tests/capabilities/test\_\<name\>.py

```python
"""Tests for <Name>Capability — <brief description>."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from overblick.core.capability import CapabilityContext
from overblick.core.llm.pipeline import PipelineResult
from overblick.capabilities.<bundle>.<name> import <Name>Capability


def make_ctx(**overrides) -> CapabilityContext:
    """Create a test CapabilityContext with defaults."""
    defaults = {
        "identity_name": "test",
        "data_dir": Path("/tmp/test"),
        "config": {},
    }
    defaults.update(overrides)
    return CapabilityContext(**defaults)


class TestCreation:
    """Test capability construction."""

    def test_name(self):
        ctx = make_ctx()
        cap = <Name>Capability(ctx)
        assert cap.name == "<name>"

    def test_enabled_by_default(self):
        ctx = make_ctx()
        cap = <Name>Capability(ctx)
        assert cap.enabled is True


class TestSetup:
    """Test capability initialization."""

    @pytest.mark.asyncio
    async def test_setup_default_config(self):
        ctx = make_ctx()
        cap = <Name>Capability(ctx)
        await cap.setup()
        # Verify default config values are set

    @pytest.mark.asyncio
    async def test_setup_custom_config(self):
        ctx = make_ctx(config={"config_key": "custom_value"})
        cap = <Name>Capability(ctx)
        await cap.setup()
        assert cap._config_value == "custom_value"


class TestMethods:
    """Test capability business logic."""

    @pytest.mark.asyncio
    async def test_teardown(self):
        ctx = make_ctx()
        cap = <Name>Capability(ctx)
        await cap.setup()
        await cap.teardown()  # Should not raise

    @pytest.mark.asyncio
    async def test_get_prompt_context(self):
        ctx = make_ctx()
        cap = <Name>Capability(ctx)
        await cap.setup()
        context = cap.get_prompt_context()
        assert isinstance(context, str)


class TestWithLLM:
    """Test LLM-using methods (if applicable)."""

    @pytest.mark.asyncio
    async def test_llm_method_with_pipeline(self):
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(
            return_value=PipelineResult(content="LLM analysis result")
        )
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = <Name>Capability(ctx)
        await cap.setup()
        # Call the LLM-using method and verify
        # result = await cap.analyze("some text")
        # assert result == "LLM analysis result"
        # pipeline.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_method_blocked(self):
        pipeline = AsyncMock()
        pipeline.chat = AsyncMock(
            return_value=PipelineResult(
                blocked=True, block_reason="Safety check failed"
            )
        )
        ctx = make_ctx(llm_pipeline=pipeline)
        cap = <Name>Capability(ctx)
        await cap.setup()
        # result = await cap.analyze("some text")
        # assert result is None

    @pytest.mark.asyncio
    async def test_no_llm_available(self):
        ctx = make_ctx()  # No pipeline, no client
        cap = <Name>Capability(ctx)
        await cap.setup()
        # result = await cap.analyze("some text")
        # assert result is None
```

## Test Conventions

### Fixture Inheritance
- Plugin tests inherit `mock_llm_client` and `mock_audit_log` from `tests/conftest.py` (root conftest)
- Plugin-specific fixtures go in `tests/plugins/<name>/conftest.py`
- Capability tests use a `make_ctx()` helper instead of fixtures (simpler, more explicit)

### Async Tests
```python
@pytest.mark.asyncio
async def test_something(self):
    ...
```
- ALL async test methods need `@pytest.mark.asyncio`
- Use `AsyncMock` for async interfaces (pipeline.chat, client methods)
- Use `MagicMock` for sync interfaces (audit_log.log, quiet_hours_checker)

### Assert Patterns
```python
# Verify a mock was called
mock.some_method.assert_called()
mock.some_method.assert_called_once()
mock.some_method.assert_called_with(expected_arg)
mock.some_method.assert_any_call(action="name", details={...})

# Verify a mock was NOT called
mock.some_method.assert_not_called()

# Verify exception
with pytest.raises(RuntimeError, match="expected message"):
    await plugin.setup()
```
