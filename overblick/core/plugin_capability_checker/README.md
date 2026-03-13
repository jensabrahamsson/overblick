# Plugin Capability Checker

Minimal permission system for plugin resource access (beta).

## Overview

The Plugin Capability Checker provides a lightweight permission system that allows identity administrators to control which resources plugins can access.

**Key features:**
- Plugins declare required capabilities (e.g., `"network_outbound"`, `"secrets_access"`)
- Users grant capabilities per identity and per plugin in identity YAML
- Missing grants trigger warnings in logs (default behavior)
- Strict mode (`OVERBLICK_STRICT_CAPABILITIES=1`) raises `PermissionError` for missing grants
- Standard capability definitions with clear descriptions

## Standard Capabilities

| Capability | Description |
|------------|-------------|
| `network_outbound` | Make HTTP/HTTPS requests to external services |
| `network_inbound` | Accept incoming network connections |
| `filesystem_read` | Read files outside plugin data directory |
| `filesystem_write` | Write files outside plugin data directory |
| `shell_execute` | Execute shell commands or subprocesses |
| `email_send` | Send emails via SMTP |
| `email_receive` | Receive emails (IMAP/POP3) |
| `secrets_access` | Read secrets via `ctx.get_secret()` |
| `llm_high_priority` | Use high-priority LLM queue (gateway) |
| `llm_unlimited` | Bypass rate limits for LLM calls |
| `database_write` | Write to central database |
| `ipc_send` | Send IPC messages to other identities |

## Plugin Declaration

Plugins declare required capabilities via the `REQUIRED_CAPABILITIES` class variable:

```python
class MyPlugin(PluginBase):
    REQUIRED_CAPABILITIES = ["network_outbound", "secrets_access"]
    
    async def setup(self, ctx: PluginContext):
        # Plugin setup
        pass
```

## Identity Configuration

Identity administrators grant capabilities in the identity YAML file (`personality.yaml`):

```yaml
plugin_capabilities:
  telegram:
    network_outbound: true
    secrets_access: true
  email_agent:
    email_send: true
    secrets_access: true
  my_plugin:
    network_outbound: true
    filesystem_write: true
```

**Notes:**
- Plugin names must match the plugin's `PLUGIN_NAME` (usually the directory name in `overblick/plugins/`)
- Capability values are booleans (`true`/`false`)
- Missing grants trigger warnings (see below)
- Unknown capabilities are logged as warnings

## Behavior

### Default (Warning Mode)
When a plugin requires capabilities that aren't granted:
1. A warning is logged with the missing capabilities
2. The plugin continues to load
3. The missing capabilities may cause runtime failures

Example warning:
```
WARNING: Plugin 'telegram' missing capability grants: network_outbound (identity: anomal). Add to identity YAML:
plugin_capabilities:
  telegram:
    network_outbound: true  # Make HTTP/HTTPS requests to external services
```

### Strict Mode
Set environment variable `OVERBLICK_STRICT_CAPABILITIES=1` to enable strict enforcement:

```bash
export OVERBLICK_STRICT_CAPABILITIES=1
python -m overblick run anomal
```

In strict mode:
- Missing grants raise `PermissionError` during plugin loading
- Unknown capabilities also raise `PermissionError`
- Plugins with insufficient grants won't load

## Migration from No-Grants to Explicit Grants

### Current State (Beta)
The capability system is in beta. By default, missing grants only produce warnings.

### Recommended Migration Path
1. **Audit existing plugins**: Check each plugin's `REQUIRED_CAPABILITIES` declaration
2. **Update identity YAMLs**: Add `plugin_capabilities` section for each identity
3. **Test with warnings**: Run identities and check logs for missing grants
4. **Enable strict mode**: Once all grants are configured, set `OVERBLICK_STRICT_CAPABILITIES=1`

### Example Migration

**Before migration:** No `plugin_capabilities` section in identity YAML.

**After migration:**
```yaml
# personality.yaml
plugin_capabilities:
  telegram:
    network_outbound: true
    secrets_access: true
  email_agent:
    email_send: true
    secrets_access: true
  github:
    network_outbound: true
    secrets_access: true
  dev_agent:
    network_outbound: true
    shell_execute: true
    secrets_access: true
```

## Integration with Permission System

The Plugin Capability Checker is designed to integrate with the broader permission system in the future:

- **Current**: Capability grants checked at plugin load time
- **Future**: Runtime permission checks for sensitive operations
- **Future**: Granular resource-level permissions (e.g., specific API endpoints, file paths)

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OVERBLICK_STRICT_CAPABILITIES` | `0` (false) | Enable strict mode (`1` = true) |

### Code Configuration

The capability checker is instantiated by the orchestrator for each identity. Configuration is loaded from the identity YAML's `plugin_capabilities` section.

## Testing

Capability checking is tested in `tests/core/test_plugin_capability_checker.py`. Tests verify:

- Warning logging for missing grants
- `PermissionError` raising in strict mode
- Unknown capability detection
- YAML parsing and grant validation

## Related Documentation

- [AGENTS.md](../../../AGENTS.md#plugin-capability-system) - Framework overview
- [ARCHITECTURE.md](../../../ARCHITECTURE.md#plugin-capability-system) - System architecture
- [SECURITY.md](../../../SECURITY.md) - Security model and threat analysis

## Future Work

1. **Runtime permission checks**: Integrate with operation-level permission system
2. **Resource-specific grants**: Allow grants for specific resources (e.g., `filesystem_write: /tmp/`)
3. **Audit logging**: Log capability usage for security analysis
4. **UI integration**: Add capability management to web dashboard

---

**Status:** Beta feature (warning mode by default)  
**Last Updated:** 2026-03-13  
**Maintainer:** Core security team