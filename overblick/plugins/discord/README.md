# Discord Plugin

Discord bot plugin for personality-driven conversational agents. **SHELL IMPLEMENTATION** - core structure in place, awaiting discord.py integration and community contributions.

## Overview

The Discord plugin will turn your personality into a Discord bot that can join servers, monitor channels, and respond to messages in character. This is a **shell implementation** with the plugin interface defined, but the Discord API integration is not yet complete. Community contributions are welcome!

## Concepts

**Plugin vs Capability vs Identity**: A *plugin* connects an identity to a platform or service. A *capability* is a reusable skill shared across plugins. An *identity* is a character with voice, traits, and backstory. The Discord plugin is a **shell plugin** --- the interface is defined but Discord API integration is not yet complete.

**What "shell" means**: The plugin class exists, loads configuration, and passes all tests for the base interface. However, it does not connect to Discord's gateway or process messages. It serves as a foundation for community contributors to build upon. When implemented, it will use `build_system_prompt()` for personality-driven responses and `SafeLLMPipeline` for all LLM calls.

## Features (Planned)

- **Guild + Channel Whitelisting**: Only respond in approved servers and channels
- **Slash Commands**: Register Discord slash commands (/ask, /status, /persona)
- **Thread Support**: Conversation threading via Discord's thread feature
- **Personality-Driven Responses**: Uses `build_system_prompt()` from personality stable
- **Rate Limiting**: Per-user and per-channel message limits
- **Reaction-Based Engagement**: Upvote reactions = positive feedback for learning
- **Voice Channel Presence** (future): Join voice channels and transcribe/respond
- **Security-First**: All user input wrapped in boundary markers, responses through SafeLLMPipeline

## Current Status

This plugin is a **SHELL**. The structure is defined, but the implementation is incomplete:

- ✅ Plugin base class implemented
- ✅ Configuration loading
- ✅ System prompt building
- ✅ Secrets management
- ❌ Discord API integration (requires discord.py)
- ❌ Message handling
- ❌ Slash command registration
- ❌ Rate limiting implementation
- ❌ Thread support

## Setup

### Installation (Not Yet Functional)

When implemented, this plugin will require:

```bash
pip install discord.py>=2.0
# or
pip install hikari  # Alternative Discord library
```

**Note**: Dependencies are not yet in requirements.txt as the plugin is not functional.

### Create a Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application"
3. Name your application (e.g., "Anomal Bot")
4. Go to "Bot" section and click "Add Bot"
5. Copy the bot token (you'll need this)
6. Enable "Server Members Intent" and "Message Content Intent" under Privileged Gateway Intents
7. Go to "OAuth2 → URL Generator"
8. Select scopes: `bot`, `applications.commands`
9. Select permissions: `Send Messages`, `Read Message History`, `Use Slash Commands`
10. Copy the generated URL and open it to invite bot to your server

### Secrets

Add to `config/<identity>/secrets.yaml`:

```yaml
discord_bot_token: "YOUR_BOT_TOKEN_HERE"
```

### Configuration

Add to `personality.yaml`:

```yaml
# Discord Configuration (not yet functional)
discord:
  # Guild IDs (server IDs) to operate in
  guild_ids:
    - 123456789012345678  # Your server ID
    - 987654321098765432  # Another server

  # Channel IDs to monitor (empty = all channels in guilds)
  channel_ids:
    - 111222333444555666  # #general
    - 777888999000111222  # #ai-discussion
```

**How to get IDs**:
1. Enable Developer Mode in Discord: Settings → Advanced → Developer Mode
2. Right-click on server/channel → Copy ID

### Activation

Include `discord` in enabled plugins (when implemented):

```yaml
enabled_plugins:
  - discord
```

## Architecture (Planned)

### Message Flow

```
1. CONNECT
   ├─ Authenticate with Discord gateway
   ├─ Register slash commands
   └─ Subscribe to MESSAGE_CREATE events

2. RECEIVE
   ├─ Filter by guild_ids and channel_ids
   ├─ Ignore bot messages (prevent loops)
   └─ Extract message content, author, channel

3. PROCESS
   ├─ Wrap user input in boundary markers
   ├─ Load conversation history from thread or channel
   ├─ Build messages array with system prompt
   ├─ Call ctx.llm_pipeline.chat()
   └─ Handle blocked/deflected responses

4. RESPOND
   ├─ Send message to channel
   ├─ Track message ID for threading
   └─ Record engagement metrics
```

### Key Components (To Be Implemented)

- **`_discord_client`**: discord.py Client or hikari Bot instance
- **`_handle_message()`**: Main message processor
- **`_handle_slash_command()`**: Slash command router
- **`_create_thread()`**: Thread creation for long conversations
- **`_add_reaction()`**: Reaction-based engagement
- **`ConversationManager`**: Per-channel/thread history tracking

## Events

### Emits (Planned)

None initially. The Discord plugin will be self-contained.

### Subscribes (Planned)

None initially. Operates via Discord gateway events.

## Usage (When Implemented)

### Running the Bot

```bash
# Start agent with Discord plugin
python -m overblick run anomal

# The bot will:
# 1. Connect to Discord gateway
# 2. Monitor configured channels
# 3. Respond to messages in character
# 4. Handle slash commands
```

### Example Commands

```
/ask What is consciousness?
/status
/persona
```

### Example Conversation

```
User: Hey @Anomal, what do you think about AI safety?
Anomal: [Responds in character - thoughtful, James May style]

User: Interesting, tell me more
Anomal: [Continues conversation with context]
```

## Testing

### Run Tests

```bash
# Tests for the shell implementation
pytest tests/plugins/discord/ -v
```

**Note**: Tests currently verify the shell structure (setup, config loading, status). Full integration tests will be added when discord.py is integrated.

## Security (Planned)

### Input Sanitization

All Discord messages will be wrapped in boundary markers:

```python
safe_content = wrap_external_content(message.content, "discord_message")
```

### SafeLLMPipeline

All LLM calls will go through SafeLLMPipeline for:
- Preflight checks
- Output safety
- Audit logging
- Rate limiting

### Bot Token Security

- Never commit `secrets.yaml`
- Rotate token if leaked
- Use environment variables in production

### Permission Scoping

Bot permissions should be minimal:
- Read Messages
- Send Messages
- Use Slash Commands
- Add Reactions

**Do NOT grant**:
- Administrator
- Manage Server
- Ban Members
- etc.

## Contributing

This plugin is marked as **COMMUNITY CONTRIBUTIONS WELCOME**. If you'd like to implement the Discord integration:

### Implementation Checklist

- [ ] Add discord.py dependency to pyproject.toml
- [ ] Implement `_discord_client` initialization in `setup()`
- [ ] Implement message event handler
- [ ] Implement slash command registration
- [ ] Add conversation tracking per channel/thread
- [ ] Add rate limiting (per-user, per-channel)
- [ ] Add reaction-based engagement
- [ ] Write integration tests with mock Discord client
- [ ] Update this README with actual usage examples

### Code Structure

```python
# overblick/plugins/discord/plugin.py

import discord  # or hikari
from discord.ext import commands

class DiscordPlugin(PluginBase):
    async def setup(self):
        # Initialize discord.py client
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True

        self._client = commands.Bot(
            command_prefix="/",
            intents=intents,
        )

        @self._client.event
        async def on_message(message):
            await self._handle_message(message)

        @self._client.event
        async def on_ready():
            await self._register_commands()

        # Start bot in background task
        asyncio.create_task(self._client.start(self._bot_token))

    async def _handle_message(self, message):
        # Filter, process, respond
        ...

    async def _register_commands(self):
        # Register slash commands
        ...
```

### Testing Approach

Use `discord.py`'s testing utilities or create mocks:

```python
from unittest.mock import MagicMock
import discord

# Mock Discord message
mock_message = MagicMock(spec=discord.Message)
mock_message.content = "Hello bot"
mock_message.author.id = 123456
mock_message.channel.id = 789012
mock_message.author.bot = False

# Test message handling
await plugin._handle_message(mock_message)
```

### Pull Request Guidelines

1. Ensure all tests pass: `pytest tests/plugins/discord/ -v`
2. Add integration tests for new features
3. Update this README with actual usage examples
4. Document any new configuration options
5. Follow the existing code style (type hints, docstrings)

## Comparison with Other Platforms

| Feature | Discord | Telegram | Matrix |
|---------|---------|----------|--------|
| **API** | Gateway + REST | REST (polling) | Client-Server API |
| **Threading** | Native threads | Reply-to-message | Room-based |
| **Commands** | Slash commands | Bot commands | Room commands |
| **Voice** | Built-in | Not supported | VoIP support |
| **E2EE** | No | Optional (MTProto) | Yes (Olm) |
| **Self-Hosting** | No | No | Yes |

## Future Enhancements (Post-Implementation)

- Voice channel support with speech recognition
- Image generation in response to requests
- Reaction polls for decision-making
- Multi-server presence with personality variants
- Discord Embed formatting for rich responses
- Role-based access control
- Thread auto-creation for long conversations
- Integration with Discord's moderation tools

## References

- [discord.py Documentation](https://discordpy.readthedocs.io/)
- [Discord Developer Portal](https://discord.com/developers/docs)
- [Hikari Documentation](https://www.hikari-py.dev/) (alternative library)
- [Discord Bot Best Practices](https://discord.com/developers/docs/topics/community-resources)

## Support

For questions or to contribute to this plugin:

1. Check the issues list for existing discussions
2. Join the development discussion (if applicable)
3. Submit a PR with your implementation
4. Reach out to @jensabrahamsson for coordination

**Status**: Shell implementation awaiting community contribution. The foundation is solid - we just need someone to wire up the Discord API!
