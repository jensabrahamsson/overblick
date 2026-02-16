# Plugin Quality & Security Checklist

Use this checklist when creating or reviewing Överblick plugins.

## Security Checklist

### External Content
- [ ] All user/external input wrapped with `wrap_external_content(content, source)`
- [ ] Import: `from overblick.core.security.input_sanitizer import wrap_external_content`
- [ ] Source label is descriptive (e.g., `"telegram_message"`, `"email_body"`, `"api_response"`)

### LLM Usage
- [ ] Uses `ctx.llm_pipeline` (SafeLLMPipeline), NOT raw `ctx.llm_client`
- [ ] Handles `result.blocked` case (pipeline may block requests)
- [ ] Uses `result.deflection` for safe fallback responses when blocked
- [ ] Sets meaningful `audit_action` and `audit_details` in pipeline calls

### Secrets
- [ ] All secrets loaded via `ctx.get_secret(key)`, never hardcoded
- [ ] Missing secrets raise `RuntimeError` in `setup()` (fail-fast)
- [ ] No secrets in log messages or error traces
- [ ] Secret keys follow convention: `<plugin>_<key>` (e.g., `telegram_bot_token`)

### Permissions
- [ ] Permission-gated actions check `ctx.permissions.is_allowed(action)` before executing
- [ ] Actions recorded via `ctx.permissions.record_action(action)`

### Audit Logging
- [ ] Plugin setup logs `action="plugin_setup"`
- [ ] Significant actions logged via `ctx.audit_log.log(action, details={...})`
- [ ] Failed actions logged with `success=False` and `error=str(e)`
- [ ] No sensitive data in audit details (no secrets, tokens, passwords)

### Rate Limiting
- [ ] Plugin respects quiet hours: `ctx.quiet_hours_checker.is_quiet_hours()`
- [ ] Per-user rate limiting for user-facing plugins
- [ ] Graceful handling of external API rate limits (catch, log, retry later)

## Quality Checklist

### Structure
- [ ] Plugin lives in `overblick/plugins/<name>/`
- [ ] Has `__init__.py` that re-exports the plugin class
- [ ] Has `plugin.py` with the main plugin class
- [ ] Plugin class extends `PluginBase`
- [ ] Plugin has `name` class attribute (lowercase)
- [ ] Registered in `overblick/core/plugin_registry.py` `_DEFAULT_PLUGINS`

### Code Standards
- [ ] All code, comments, logs, variable names in **English**
- [ ] Type hints on all public methods
- [ ] Docstrings on class and public methods
- [ ] Uses `logging.getLogger(__name__)` for logging
- [ ] No print statements (use logger)
- [ ] Python 3.13+ compatible

### Lifecycle
- [ ] `__init__` only stores state — no I/O
- [ ] `setup()` is async and handles failures gracefully
- [ ] `tick()` is async, quick, respects quiet hours
- [ ] `teardown()` closes all resources (clients, connections, files)
- [ ] Teardown is safe to call multiple times

### Testing
- [ ] Test directory at `tests/plugins/<name>/`
- [ ] Has `conftest.py` with proper mock fixtures
- [ ] Tests for setup (including missing secrets)
- [ ] Tests for tick (normal flow and edge cases)
- [ ] Tests for teardown
- [ ] Tests use `pytest.mark.asyncio` for async tests
- [ ] Tests use mock `PluginContext` (never real framework services)
- [ ] Tests pass: `./venv/bin/python3 -m pytest tests/plugins/<name>/ -v`

### Capability Integration (if applicable)
- [ ] Capabilities loaded via `CapabilityRegistry.default()`
- [ ] Shared capabilities checked first: `getattr(self.ctx, "capabilities", {})`
- [ ] Capability context created with `CapabilityContext.from_plugin_context(ctx)`
- [ ] Capabilities torn down in reverse order in plugin teardown

## Common Mistakes

1. **Using raw `llm_client` instead of `llm_pipeline`** — bypasses all security stages
2. **Forgetting to wrap external content** — leaves prompt injection vectors open
3. **Hardcoding secrets** — use `ctx.get_secret()` always
4. **Not registering in `_DEFAULT_PLUGINS`** — plugin won't load
5. **Doing I/O in `__init__`** — context may not be fully populated yet
6. **Not handling quiet hours** — agent acts when it shouldn't
7. **Swedish in code** — all code must be in English (see CLAUDE.md)
