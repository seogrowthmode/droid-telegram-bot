# Droid Telegram Bot - Enhanced Edition

A Telegram bot that interfaces with [Factory's Droid CLI](https://factory.ai), allowing you to interact with Droid via Telegram messages.

> **Fork:** [seogrowthmode/droid-telegram-bot](https://github.com/seogrowthmode/droid-telegram-bot) | **Original:** [factory-ben/droid-telegram-bot](https://github.com/factory-ben/droid-telegram-bot)

## Features

### Core Features
- 💬 **Chat with Droid** - Send messages and get AI-powered responses
- ⚡ **Live Streaming** - Watch tool calls in real-time as Droid works
- 📂 **Session Management** - Persistent sessions with working directory context
- 🔐 **Access Control** - Restrict bot access to specific Telegram users
- 🎚️ **Autonomy Levels** - Control how much freedom Droid has (off/low/medium/high/unsafe)
- 🔧 **Git Integration** - Quick `/git` commands for common operations

### Enhanced Features (This Fork)
- 🔄 **Auto Git Sync** - Automatically pull before tasks and push after changes
- 📁 **Project Shortcuts** - Quick project switching with `/proj` command
- 🎤 **Voice Messages** - Send voice notes, get them transcribed and processed
- 🔃 **Auto-Restart** - Development mode with automatic reload on file changes

## Prerequisites

- Python 3.10+
- A [Factory](https://factory.ai) account and API key
- [Droid CLI](https://docs.factory.ai) installed
- Telegram bot token (from [@BotFather](https://t.me/botfather))
- Your Telegram user ID (from [@userinfobot](https://t.me/userinfobot))
- ffmpeg (optional, for voice messages)

## Quick Start

### 1. Install Droid CLI

```bash
# Install the Droid CLI
curl -fsSL https://factory.ai/install.sh | sh

# Verify installation
droid --version
```

### 2. Get a Factory API Key

1. Sign up at [factory.ai](https://factory.ai)
2. Go to Settings → API Keys
3. Create a new API key and copy it

### 3. Clone and Install

```bash
git clone https://github.com/factory-ben/droid-telegram-bot.git
cd droid-telegram-bot
pip install -r requirements.txt
```

### 4. Configure Environment

```bash
cp .env.example .env
# Edit .env with your values
```

Required environment variables:
- `FACTORY_API_KEY` - Your Factory API key (from step 2)
- `TELEGRAM_BOT_TOKEN` - Your bot token from BotFather
- `TELEGRAM_ALLOWED_USER_IDS` - Comma-separated Telegram user IDs

### 5. Run

```bash
# Using start script (recommended - handles env vars properly)
./start.sh

# Development mode with auto-restart on file changes
./watch.sh

# Or direct (requires env vars to be exported)
python bot.py
```

### 6. Optional: Voice Message Support

To enable voice message transcription, install ffmpeg and OpenAI Whisper:

```bash
# macOS
brew install ffmpeg
pip install openai-whisper

# Ubuntu/Debian
sudo apt install ffmpeg
pip install openai-whisper
```

Note: First voice message may be slow as Whisper downloads the model (~140MB).

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FACTORY_API_KEY` | ✅ | - | Your Factory API key ([get one here](https://factory.ai)) |
| `TELEGRAM_BOT_TOKEN` | ✅ | - | Telegram bot token from @BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | ✅ | - | Comma-separated Telegram user IDs |
| `DROID_PATH` | ❌ | `droid` | Path to Droid CLI |
| `DROID_DEFAULT_CWD` | ❌ | `~` | Default working directory |
| `DROID_LOG_FILE` | ❌ | `/var/log/droid-telegram/bot.log` | Log file path |
| `DROID_SESSIONS_FILE` | ❌ | `/var/lib/droid-telegram/sessions.json` | Sessions file |
| `DROID_PROJECT_SHORTCUTS` | ❌ | `{}` | JSON object of project shortcuts (e.g., `'{"myapp":"~/dev/myapp"}'`) |
| `DROID_AUTO_GIT_PULL` | ❌ | `true` | Auto git pull before tasks |
| `DROID_AUTO_GIT_PUSH` | ❌ | `false` | Auto commit & push after tasks |

## Commands

### Core Commands
| Command | Description |
|---------|-------------|
| `/start` | Welcome message and quick help |
| `/help` | Detailed help |
| `/new` | Start new session (optionally in directory) |
| `/cwd` | Show current working directory |
| `/stream` | Toggle live tool updates on/off |
| `/auto` | Set autonomy level (off/low/medium/high/unsafe) |
| `/stop` | Stop currently running task |
| `/status` | Bot and Droid status |
| `/session` | List/switch sessions |
| `/git` | Run git commands in current directory |

### Enhanced Commands (This Fork)
| Command | Description |
|---------|-------------|
| `/proj` | Switch project (defaults: high, opus, sync) |
| `/sync` | Toggle auto git sync options |
| `/pull` | Manually pull latest changes |
| `/push` | Commit all changes and push |
| `/add` | Add task to queue |
| `/queue` | View task queue |
| `/run` | Start processing queue |
| `/pause` | Pause queue processing |
| `/clear` | Clear all queued tasks |
| `/skip` | Skip current task |


## Autonomy Levels

Control how much freedom Droid has with the `/auto` command:

| Level | Description |
|-------|-------------|
| `off` | Read-only mode (default) - no tool execution |
| `low` | Safe tools only |
| `medium` | Most tools allowed |
| `high` | All tools, asks for risky ones |
| `unsafe` | Skip all permission checks |

## Usage Tips

### General
- **Reply to continue** - Reply to any bot message to continue that session
- **Working directories** - Use `/new ~/projects/myapp` to set context
- **Live updates** - Watch Droid's progress with streaming enabled (default)
- **Autonomy control** - Use `/auto high` to enable tool execution

### Project Shortcuts (Enhanced)
Set up shortcuts in your `.env` or `start.sh`:
```bash
DROID_PROJECT_SHORTCUTS='{"myapp":"~/dev/myapp","website":"~/dev/website"}'
```

Then use the combined command for quick setup:
```
/proj myapp high sonnet sync
```
This switches to project, sets autonomy to high, uses Sonnet model, and enables auto-push - all in one command. Perfect for mobile!

### Git Sync (Enhanced)
- Auto-pull is enabled by default - always work on latest code
- Enable auto-push with `/sync push` or set `DROID_AUTO_GIT_PUSH=true`
- Manual control with `/pull` and `/push "commit message"`

### Voice Messages (Enhanced)
Just send a voice note! The bot will:
1. Transcribe it using Whisper (if installed)
2. Send the transcription to Droid
3. Return the response

Great for quick ideas while away from keyboard.

## Production Deployment

### systemd Service

```bash
# Copy service file
sudo cp droid-telegram.service /etc/systemd/system/

# Edit with your environment variables
sudo systemctl edit droid-telegram

# Enable and start
sudo systemctl enable droid-telegram
sudo systemctl start droid-telegram
```

### Docker (coming soon)

```bash
docker run -e TELEGRAM_BOT_TOKEN=xxx -e TELEGRAM_ALLOWED_USER_IDS=123 droid-telegram
```

## Security Notes

- **Never commit tokens** - Use environment variables or `.env` files
- **Restrict access** - Always set `TELEGRAM_ALLOWED_USER_IDS`
- **Review permissions** - The bot can execute commands via Droid

## License

MIT License - see [LICENSE](LICENSE)

## Contributing

Contributions welcome! Please open an issue or PR.
