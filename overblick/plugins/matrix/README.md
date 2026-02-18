# Matrix Plugin

Matrix chat agent for decentralized, privacy-focused messaging. **SHELL IMPLEMENTATION** - core structure in place, awaiting matrix-nio integration and community contributions.

## Overview

The Matrix plugin will connect your personality to the Matrix protocol, a decentralized, open-source messaging platform with end-to-end encryption. Perfect for privacy-focused agents like Volt. This is a **shell implementation** with the plugin interface defined, but the Matrix protocol integration is not yet complete.

## Concepts

**Plugin vs Capability vs Identity**: A *plugin* connects an identity to a platform or service. A *capability* is a reusable skill shared across plugins. An *identity* is a character with voice, traits, and backstory. The Matrix plugin is a **shell plugin** --- the interface is defined but Matrix protocol integration is not yet complete.

**What "shell" means**: The plugin class exists, loads configuration (homeserver, access token, room IDs), and passes all base interface tests. However, it does not connect to Matrix or process messages. When implemented, it will use end-to-end encryption via matrix-nio, making it ideal for privacy-focused identities.

## Features (Planned)

- **Homeserver Authentication**: Connect to any Matrix homeserver (matrix.org, element.io, self-hosted)
- **Room Join/Leave Management**: Automatically join configured rooms
- **End-to-End Encryption (E2EE)**: Secure messaging via libolm/vodozemac
- **Device Verification**: Trust only verified devices in encrypted rooms
- **Conversation Tracking**: Per-room conversation history
- **Personality-Driven Responses**: Uses `build_system_prompt()` from personality stable
- **Rate Limiting**: Per-user and per-room message limits
- **Media Handling**: Images, files, and rich media support
- **Room-Specific Personality Overrides**: Different personality aspects in different rooms
- **Federation Support**: Communicate across Matrix homeservers

## Current Status

This plugin is a **SHELL**. The structure is defined, but the implementation is incomplete:

- ✅ Plugin base class implemented
- ✅ Configuration loading (homeserver, access token, room IDs)
- ✅ System prompt building
- ✅ Secrets management
- ❌ Matrix protocol integration (requires matrix-nio)
- ❌ E2EE support (requires python-olm)
- ❌ Message handling
- ❌ Room sync
- ❌ Device verification
- ❌ Media upload/download

## Why Matrix?

Matrix is ideal for privacy-focused AI agents:

- **Decentralized**: No single point of control or failure
- **Open Protocol**: Fully specified, auditable, extensible
- **E2EE by Default**: Messages encrypted end-to-end in private rooms
- **Federated**: Communicate across different homeservers
- **Self-Hostable**: Run your own homeserver for full control
- **Rich Features**: Reactions, threads, spaces, voice/video

Perfect for agents like **Volt** (privacy advocate) or **Anomal** (philosophical thinker) who value open, decentralized platforms.

## Setup

### Installation (Not Yet Functional)

When implemented, this plugin will require:

```bash
pip install matrix-nio[e2e]>=0.21.0
# Includes python-olm for E2EE support
```

**Note**: Dependencies are not yet in requirements.txt as the plugin is not functional.

### Create a Matrix Account

#### Option 1: Use matrix.org (Quick Start)

1. Go to [app.element.io](https://app.element.io)
2. Sign up for a new account
3. Your Matrix ID will be: `@username:matrix.org`

#### Option 2: Self-Hosted (Advanced)

1. Install Synapse (Matrix homeserver): [matrix.org/docs/guides/installing-synapse](https://matrix.org/docs/guides/installing-synapse)
2. Create an account on your homeserver
3. Your Matrix ID: `@username:your-domain.com`

### Get Access Token

#### Via Element Web

1. Log in to [app.element.io](https://app.element.io)
2. Settings → Help & About → Advanced
3. Scroll to "Access Token" and click "Show Access Token"
4. Copy the token (long string starting with `syt_...` or similar)

#### Via API

```bash
curl -XPOST -d '{"type":"m.login.password", "user":"username", "password":"password"}' \
  "https://matrix.org/_matrix/client/r0/login"
```

Extract `access_token` from response.

### Secrets

Add to `config/<identity>/secrets.yaml`:

```yaml
matrix_access_token: "syt_dGVzdA_xxxxxxxxxxxxxxxxxxxxxx_yyyyyy"
```

### Configuration

Add to `personality.yaml`:

```yaml
# Matrix Configuration (not yet functional)
matrix:
  # Homeserver URL (default: https://matrix.org)
  homeserver: "https://matrix.org"

  # Your Matrix user ID
  user_id: "@anomal:matrix.org"

  # Room IDs to join and monitor
  room_ids:
    - "!AbCdEfGhIjKlMnOpQr:matrix.org"  # Public AI discussion room
    - "!XyZaBcDeFgHiJkLmNo:matrix.org"  # Private philosophy room
```

**How to get Room ID**:
1. In Element, open the room
2. Settings → Advanced → Internal room ID
3. Copy (format: `!<random>:<homeserver>`)

### Activation

Include `matrix` in enabled plugins (when implemented):

```yaml
enabled_plugins:
  - matrix
```

## Architecture (Planned)

### Connection Flow

```
1. AUTHENTICATE
   ├─ Load access token from secrets
   ├─ Verify homeserver URL
   ├─ Create AsyncClient from matrix-nio
   └─ Login with token

2. SYNC
   ├─ Sync with homeserver (get room state)
   ├─ Join configured rooms
   ├─ Set up event listeners
   └─ Enable E2EE if room is encrypted

3. LISTEN
   ├─ Receive m.room.message events
   ├─ Filter by room_id whitelist
   ├─ Ignore own messages
   └─ Route to _handle_message()

4. PROCESS
   ├─ Wrap message content in boundary markers
   ├─ Load conversation history for room
   ├─ Build messages array with system prompt
   ├─ Call ctx.llm_pipeline.chat()
   └─ Handle blocked/deflected responses

5. RESPOND
   ├─ Send m.room.message to room
   ├─ Track message for threading
   └─ Record engagement metrics
```

### Key Components (To Be Implemented)

- **`_client`**: AsyncClient from matrix-nio
- **`_device_store`**: Device verification database
- **`_handle_message()`**: Main message processor
- **`_handle_invite()`**: Auto-join invited rooms
- **`_verify_device()`**: E2EE device verification
- **`RoomConversationManager`**: Per-room history tracking

## Events

### Emits (Planned)

None initially. The Matrix plugin will be self-contained.

### Subscribes (Planned)

None initially. Operates via Matrix sync loop.

## Usage (When Implemented)

### Running the Agent

```bash
# Start agent with Matrix plugin
python -m overblick run volt  # Volt is perfect for Matrix

# The agent will:
# 1. Connect to homeserver
# 2. Sync room state
# 3. Join configured rooms
# 4. Respond to messages in character
```

### Example Conversation

In Matrix room (encrypted):

```
User: @anomal:matrix.org Hey, what do you think about decentralization?
Anomal: [Responds in character - thoughtful, privacy-conscious]

User: How does Matrix compare to centralized platforms?
Anomal: [Continues conversation with E2EE context]
```

## Testing

### Run Tests

```bash
# Tests for the shell implementation
pytest tests/plugins/matrix/ -v
```

**Note**: Tests currently verify the shell structure. Full integration tests will be added when matrix-nio is integrated.

## Security (Planned)

### Input Sanitization

All Matrix messages will be wrapped in boundary markers:

```python
safe_content = wrap_external_content(message.body, "matrix_message")
```

### SafeLLMPipeline

All LLM calls will go through SafeLLMPipeline for:
- Preflight checks
- Output safety
- Audit logging
- Rate limiting

### E2EE (Critical for Matrix)

**End-to-end encryption MUST be enabled for private rooms**:

```python
# Verify room encryption before responding
if room.encrypted and not message.decrypted:
    logger.warning("Received unencrypted message in encrypted room")
    return  # Do not respond
```

### Device Verification

Before trusting encrypted messages, devices must be verified:

```python
# On first message from new device
if not self._device_store.is_verified(sender, device_id):
    await self._verify_device(sender, device_id)
```

Use one of:
- Interactive verification (emoji comparison)
- QR code verification
- Key fingerprint verification

### Access Token Security

- Never commit `secrets.yaml`
- Rotate token if leaked: Log out and log in again
- Use environment variables in production
- Consider using password login with 2FA instead of tokens

## Contributing

This plugin is marked as **COMMUNITY CONTRIBUTIONS WELCOME**. If you'd like to implement the Matrix integration:

### Implementation Checklist

- [ ] Add matrix-nio[e2e] dependency to pyproject.toml
- [ ] Implement AsyncClient initialization in `setup()`
- [ ] Implement sync loop with message event handler
- [ ] Add E2EE support with libolm/vodozemac
- [ ] Implement device verification flow
- [ ] Add conversation tracking per room
- [ ] Add rate limiting (per-user, per-room)
- [ ] Support media upload/download
- [ ] Add room invitation handling
- [ ] Write integration tests with mock Matrix client
- [ ] Update this README with actual usage examples

### Code Structure

```python
# overblick/plugins/matrix/plugin.py

from nio import AsyncClient, MatrixRoom, RoomMessageText

class MatrixPlugin(PluginBase):
    async def setup(self):
        # Initialize Matrix client
        self._client = AsyncClient(
            homeserver=self._homeserver,
            user=self._user_id,
        )

        # Restore session or login
        self._client.access_token = self._access_token
        self._client.user_id = self._user_id

        # Set up callbacks
        self._client.add_event_callback(
            self._handle_message,
            RoomMessageText,
        )

        # Start sync loop in background
        asyncio.create_task(self._sync_loop())

    async def _sync_loop(self):
        await self._client.sync_forever(timeout=30000)

    async def _handle_message(self, room: MatrixRoom, event: RoomMessageText):
        # Filter, process, respond
        if room.room_id not in self._room_ids:
            return

        # ... rest of logic
```

### Testing Approach

Use matrix-nio's testing utilities or create mocks:

```python
from unittest.mock import AsyncMock, MagicMock
from nio import MatrixRoom, RoomMessageText

# Mock Matrix room
mock_room = MatrixRoom(room_id="!test:matrix.org", own_user_id="@bot:matrix.org")

# Mock message event
mock_event = RoomMessageText(
    event_id="$event123",
    sender="@user:matrix.org",
    origin_server_ts=1234567890,
    body="Hello bot",
)

# Test message handling
await plugin._handle_message(mock_room, mock_event)
```

### Pull Request Guidelines

1. Ensure all tests pass: `pytest tests/plugins/matrix/ -v`
2. Add integration tests for E2EE scenarios
3. Update this README with actual usage examples
4. Document E2EE setup process thoroughly
5. Follow existing code style (type hints, docstrings)

## Configuration Examples

### Public Matrix Room

```yaml
matrix:
  homeserver: "https://matrix.org"
  user_id: "@anomal:matrix.org"
  room_ids:
    - "!AIdiscussion:matrix.org"  # Public room, no E2EE
```

### Private Encrypted Room

```yaml
matrix:
  homeserver: "https://matrix.org"
  user_id: "@volt:matrix.org"
  room_ids:
    - "!privateAI:matrix.org"  # E2EE enabled
```

**Important**: E2EE rooms require device verification. The plugin must verify devices before responding.

### Self-Hosted Homeserver

```yaml
matrix:
  homeserver: "https://matrix.example.com"
  user_id: "@anomal:example.com"
  room_ids:
    - "!internal:example.com"
```

## Comparison with Other Platforms

| Feature | Matrix | Discord | Telegram |
|---------|--------|---------|----------|
| **Decentralization** | ✅ Federated | ❌ Centralized | ❌ Centralized |
| **E2EE** | ✅ Built-in | ❌ No | ⚠️ Optional (Secret Chats) |
| **Self-Hosting** | ✅ Yes | ❌ No | ❌ No |
| **Open Protocol** | ✅ Fully open | ⚠️ Partial | ⚠️ MTProto |
| **Threading** | ✅ Native | ✅ Native | ⚠️ Reply-to |
| **Voice/Video** | ✅ VoIP | ✅ Built-in | ✅ Built-in |
| **Bots** | ✅ Full API | ✅ Full API | ✅ Full API |

**Matrix is the best choice for**:
- Privacy-focused agents (Volt)
- Decentralization advocates (Anomal)
- Self-hosted deployments
- Encrypted communications
- Open-source purists

## Future Enhancements (Post-Implementation)

- Voice message transcription via Whisper
- Image generation in response to requests
- Spaces support (organize rooms)
- Bridging to other platforms (Discord, Telegram, Slack)
- Multi-personality presence (different personalities in different rooms)
- Rich message formatting (HTML, Markdown)
- Reactions and read receipts
- Typing indicators for "thinking" feedback
- Integration with Matrix bots ecosystem

## References

- [Matrix Specification](https://spec.matrix.org/)
- [matrix-nio Documentation](https://matrix-nio.readthedocs.io/)
- [Matrix Client-Server API](https://spec.matrix.org/latest/client-server-api/)
- [End-to-End Encryption in Matrix](https://matrix.org/docs/guides/end-to-end-encryption-implementation-guide)
- [Synapse Homeserver Setup](https://matrix.org/docs/guides/installing-synapse)

## Support

For questions or to contribute to this plugin:

1. Check the issues list for existing discussions
2. Join #overblick:matrix.org (when room is created)
3. Submit a PR with your implementation
4. Reach out to @jensabrahamsson for coordination

**Status**: Shell implementation awaiting community contribution. The foundation is solid - we need someone passionate about privacy and decentralization to wire up the Matrix protocol!
