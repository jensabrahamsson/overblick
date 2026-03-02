# Setting Up Telegram for Överblick

This guide walks you through connecting Telegram to Överblick — from creating your first bot to verifying that notifications flow correctly. No prior Telegram experience required.

---

## Table of Contents

1. [What is Telegram?](#1-what-is-telegram)
2. [Download & Install Telegram](#2-download--install-telegram)
3. [Get Your Telegram Chat ID](#3-get-your-telegram-chat-id)
4. [Create a Telegram Bot via @BotFather](#4-create-a-telegram-bot-via-botfather)
5. [Configure Telegram in Överblick](#5-configure-telegram-in-överblick)
6. [Verify It Works](#6-verify-it-works)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. What is Telegram?

[Telegram](https://telegram.org) is a free, cross-platform messaging app with a powerful Bot API. Unlike most messaging platforms, Telegram lets you create fully programmable bots that can send messages, receive commands, and have conversations — all through a simple HTTPS API.

Överblick uses Telegram in two ways:

- **Telegram Plugin** — turns an identity (e.g. Anomal, Cherry) into a conversational Telegram bot. Users message the bot and it responds in character.
- **TelegramNotifier Capability** — lets other plugins (like Stål's email agent) send real-time notifications to your Telegram and receive feedback via replies.

## 2. Download & Install Telegram

Download Telegram for your platform:

| Platform | Link |
|----------|------|
| iOS | [App Store](https://apps.apple.com/app/telegram-messenger/id686449807) |
| Android | [Google Play](https://play.google.com/store/apps/details?id=org.telegram.messenger) |
| macOS | [Mac App Store](https://apps.apple.com/app/telegram/id747648890) or [Direct Download](https://macos.telegram.org) |
| Windows | [telegram.org/dl](https://telegram.org/dl) |
| Linux | [telegram.org/dl](https://telegram.org/dl) |
| Web | [web.telegram.org](https://web.telegram.org) |

Creating an account requires a phone number. Telegram uses it for verification only — it won't be visible to bots.

## 3. Get Your Telegram Chat ID

Your **chat ID** is a numeric identifier that tells Överblick where to send notifications. Here's how to find it:

### Method 1: @userinfobot (easiest)

1. Open Telegram and search for **@userinfobot**
2. Send it any message (e.g. "hi")
3. It replies with your user info, including your **Id** — that number is your `telegram_chat_id`

Example reply:
```
Id: 123456789
First: Jane
Last: Doe
Lang: en
```

Your chat ID is `123456789`.

### Method 2: getUpdates API (for group chats)

If you want notifications sent to a **group chat** instead of a private chat:

1. Create the group and add your bot to it
2. Send a message in the group
3. Open this URL in your browser (replace `<TOKEN>` with your bot token from step 4):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
4. Look for `"chat":{"id":-987654321}` — group chat IDs are **negative numbers**

## 4. Create a Telegram Bot via @BotFather

[@BotFather](https://t.me/BotFather) is Telegram's official tool for creating and managing bots.

### Step-by-step

1. Open Telegram and search for **@BotFather** (look for the blue checkmark)
2. Send `/newbot`
3. Choose a **display name** for your bot (e.g. "Anomal Bot")
4. Choose a **username** — must end in `bot` (e.g. `anomal_overblick_bot`)
5. BotFather replies with your **bot token**:

```
Done! Congratulations on your new bot.
...
Use this token to access the HTTP API:
123456789:ABCdefGHIjklMNOpqrsTUVwxyz
```

**Copy the token** — you'll need it in the next step.

### Optional: customize your bot

Send these commands to @BotFather to polish your bot's profile:

| Command | What it does |
|---------|-------------|
| `/setdescription` | Short description shown when users first open the bot |
| `/setabouttext` | "About" section in the bot's profile |
| `/setuserpic` | Profile picture for the bot |
| `/setcommands` | Register slash commands for the command menu |

### Security note

**Never share your bot token publicly.** Anyone with the token can control your bot — send messages, read conversations, and impersonate it. If your token is ever compromised:

1. Open @BotFather
2. Send `/revoke`
3. Select the compromised bot
4. Update the token in Överblick's secrets

## 5. Configure Telegram in Överblick

There are two ways to configure Telegram: through the settings wizard (recommended) or manually.

### Option A: Settings Wizard (recommended)

1. Start the dashboard:
   ```bash
   python -m overblick dashboard
   ```
2. Open [http://localhost:8080/settings/](http://localhost:8080/settings/) in your browser
3. Navigate to **Step 4 — Communication**
4. Toggle **Telegram Notifications** on
5. Paste your **Bot Token** and **Chat ID**
6. Click **Test Connection** — you should receive a test message on Telegram
7. Continue through the remaining wizard steps

The wizard automatically encrypts your secrets with Fernet before saving them.

### Option B: Manual configuration

Add the following secrets to `config/secrets/<identity>.yaml` (e.g. `config/secrets/anomal.yaml`):

```yaml
# Required
telegram_bot_token: "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
telegram_chat_id: "123456789"

# Optional — restricts which Telegram user can interact with the bot.
# If omitted, defaults to telegram_chat_id (correct for private chats).
telegram_owner_id: "123456789"
```

Then enable the plugin in the identity's `personality.yaml` under the `operational` section:

```yaml
operational:
  plugins:
    - telegram
```

> **Note:** Secrets are Fernet-encrypted at rest. If you add secrets manually (in plaintext), run the provisioner or wizard to encrypt them. The master key is stored in macOS Keychain (`overblick-secrets`) with a file fallback at `config/secrets/.master_key`.

### Secret reference

| Key | Required | Description |
|-----|----------|-------------|
| `telegram_bot_token` | Yes | Bot token from @BotFather (format: `123456:ABC-DEF...`) |
| `telegram_chat_id` | Yes | Numeric chat ID where notifications are sent |
| `telegram_owner_id` | No | Numeric user ID of the bot owner. Restricts who can interact with the bot. Defaults to `telegram_chat_id` for private chats. |

### Personality config (optional)

In your identity's `personality.yaml`, you can fine-tune Telegram-specific settings:

```yaml
telegram:
  # Whitelist of allowed chat IDs (empty list = allow all)
  allowed_chat_ids: []

  # Rate limits per user (defaults shown)
  rate_limit_per_minute: 10
  rate_limit_per_hour: 60
```

## 6. Verify It Works

### Telegram Plugin (conversational bot)

1. Start your identity with the telegram plugin enabled:
   ```bash
   python -m overblick run anomal
   ```
2. Open Telegram and find your bot (search for its username)
3. Send `/start` — the bot should reply with a greeting in character
4. Send a regular message — it should respond using the identity's personality
5. Check logs for activity:
   ```bash
   tail -f logs/anomal_telegram.log
   ```

### TelegramNotifier (notification capability)

The TelegramNotifier is used by plugins like Stål's email agent to send alerts. To verify:

1. Start an identity that uses TelegramNotifier (e.g. Stål):
   ```bash
   python -m overblick run stal
   ```
2. Trigger the plugin that sends notifications (e.g. send an email that Stål monitors)
3. Check your Telegram — you should receive a notification prefixed with the identity's name:
   ```
   *[Stål]*
   New email from sender@example.com: "Meeting tomorrow"
   ```

### Dashboard verification

Open [http://localhost:8080](http://localhost:8080) and check the agent's status page — Telegram activity should be visible in the logs and event timeline.

## 7. Troubleshooting

### Bot doesn't respond to messages

1. **Verify the token is correct:**
   ```bash
   curl https://api.telegram.org/bot<TOKEN>/getMe
   ```
   If the response says `"ok": true`, the token is valid.

2. **Check that the agent is running:**
   ```bash
   ./scripts/overblick_manager.sh supervisor-status
   ```

3. **Check logs for errors:**
   ```bash
   tail -50 logs/<identity>_telegram.log
   ```

4. **Make sure you messaged the right bot** — search for the exact username you set in @BotFather.

### "Unauthorized" error in logs

The bot token is wrong or has been revoked. Generate a new one via @BotFather (`/revoke` then `/newbot` or `/token`) and update your secrets.

### Notifications not arriving (TelegramNotifier)

- **Check `telegram_chat_id`** — make sure it matches your actual chat ID (use @userinfobot to verify)
- **Check `telegram_owner_id`** filtering — if set, only messages from that user ID are accepted. For private chats, this defaults to the chat ID automatically.
- **Check the dashboard** — look for "TelegramNotifier: not configured" warnings in the agent's logs

### Rate limited by Telegram

Telegram's Bot API allows ~30 messages/second to different chats, and ~1 message/second to the same chat. Överblick's default rate limits (10/minute, 60/hour per user) are well within these bounds. If you see rate limiting:

- Check if multiple instances are running with the same bot token
- Reduce `rate_limit_per_minute` in personality.yaml if needed

### "TelegramNotifier: missing telegram_bot_token or telegram_chat_id"

The secrets file is missing or the keys are misspelled. Verify:

1. The file exists at `config/secrets/<identity>.yaml`
2. The keys are exactly `telegram_bot_token` and `telegram_chat_id` (case-sensitive)
3. The values are not empty strings

### Messages appear but bot replies are empty

- Check that the LLM backend is running (Ollama or Gateway)
- Check `logs/<identity>.log` for LLM pipeline errors
- Verify the identity has a valid `personality.yaml` with `build_system_prompt()` support

---

## Quick Reference

```
Bot Token:     Get from @BotFather → /newbot
Chat ID:       Get from @userinfobot
Owner ID:      Same as Chat ID for private chats (optional)
Secrets file:  config/secrets/<identity>.yaml
Plugin toggle: personality.yaml → operational.plugins: [telegram]
Rate limits:   personality.yaml → telegram.rate_limit_per_minute / per_hour
Wizard:        http://localhost:8080/settings/ → Step 4
Logs:          logs/<identity>_telegram.log
```
