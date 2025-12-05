<coding_guidelines>
# Droid Telegram Bot - Development Guidelines

## MANDATORY: Update Docs on Every Change

When adding/modifying features, you MUST:

1. **Update `BOT_FEATURES` dict in bot.py** (~line 93) - This auto-updates README!
2. **Update `COMMAND_DOCS` in scripts/update-readme.py** - For new commands
3. **Update `help_command()` in bot.py** - For /help text
4. **Commit and push** - Pre-commit hook auto-syncs README.md

The pre-commit hook now auto-updates BOTH:
- Commands table (from COMMAND_DOCS)
- Enhanced Features section (from BOT_FEATURES)

### Pre-commit Hook
The hook at `.github/hooks/pre-commit` automatically:
- Runs `scripts/update-readme.py` when bot.py changes
- Stages README.md if it was updated

Install with: `./scripts/setup-hooks.sh`

## Current Bot Features

### Default Settings (applied automatically)
```python
DEFAULT_AUTONOMY = "high"      # From DROID_DEFAULT_AUTONOMY env
DEFAULT_MODEL_SHORTCUT = "opus" # From DROID_DEFAULT_MODEL env  
DEFAULT_SYNC = True            # From DROID_DEFAULT_SYNC env
```

### Commands Reference

**Project Commands:**
- `/proj <shortcut>` - Switch project (uses defaults: high, opus, sync)
- `/proj <shortcut> @name` - With custom session name
- `/proj <shortcut> nosync` - Override: disable sync
- `/proj <shortcut> sonnet` - Override: use different model
- `/new [path]` - New session in directory
- `/session` - List/switch sessions

**Queue System:**
- `/add <project> <task>` - Add task to queue (uses defaults)
- `/add <project> medium sonnet <task>` - With overrides
- `/queue` - View all queued tasks
- `/run` - Start processing queue
- `/pause` - Pause queue processing
- `/skip` - Skip current task
- `/clear` - Clear all tasks

**Git Commands:**
- `/sync` - Toggle auto git pull/push
- `/pull` - Manual git pull
- `/push [msg]` - Commit and push with message

**Other:**
- `/auto [level]` - Set autonomy (off/low/medium/high/unsafe)
- `/status` - Bot and session status
- `/stop` - Stop running task
- `/cwd` - Show current directory
- `/git [cmd]` - Run git commands

**Special Features:**
- Smart Voice Routing - Detects intent from voice messages
- Inline buttons - Quick autonomy/model selection on mobile

### Smart Voice Triggers (modify in bot.py ~line 92)
```python
VOICE_TRIGGERS = {
    "add_task": ["add a task", "add task", "queue up", "queue task", ...],
    "switch_project": ["switch to", "go to", "open project", ...],
    "show_queue": ["show queue", "what's in queue", "list tasks", ...],
    "run_queue": ["run queue", "start queue", "process tasks", ...],
    "pause_queue": ["pause queue", "stop queue", ...],
    "clear_queue": ["clear queue", "empty queue", ...],
}
```

**Example voice commands:**
- "Add a task on chadix to build the homepage" → `/add chadix build the homepage`
- "Switch to chadix" → `/proj chadix`
- "What's in my queue" → `/queue`
- "Run the queue" → `/run`

### Model Shortcuts
```python
MODEL_SHORTCUTS = {
    "opus": "claude-sonnet-4-20250514",
    "sonnet": "claude-sonnet-4-20250514", 
    "haiku": "claude-haiku",
    "gpt": "gpt-4.1",
    "codex": "codex-1",
    "gemini": "gemini-3-pro-preview",
    "glm": "glm-4.6",
}
```

## Project Structure

```
bot.py              - Main bot code (~1800 lines)
start.sh            - Startup with env vars and lock file
watch.sh            - Auto-restart on file changes
.env                - Secrets (NEVER COMMIT)
sessions.json       - Session persistence (NEVER COMMIT)
AGENTS.md           - This file (AI guidelines)
README.md           - Auto-updated by pre-commit hook
scripts/
  update-readme.py  - Generates README command tables
  setup-hooks.sh    - Installs pre-commit hook
.github/hooks/
  pre-commit        - Auto-updates README on commit
```

## Adding New Features - Checklist

1. [ ] Add handler function in bot.py
2. [ ] Register in `main()` with `CommandHandler("name", handler)`
3. [ ] **Add to `BOT_FEATURES` dict** in bot.py (~line 93) ← AUTO-UPDATES README!
4. [ ] Add to `COMMAND_DOCS` in `scripts/update-readme.py`
5. [ ] Update `help_command()` in bot.py
6. [ ] Test syntax: `python3 -m py_compile bot.py`
7. [ ] Test in Telegram
8. [ ] Commit and push → README auto-updates!

## Code Conventions

- Section headers: `# ===` separators for major features
- Logging: `logger.info()` for events, `logger.error()` for failures
- Auth check: Always start handlers with `if not is_authorized(): return`
- HTML mode: Use `parse_mode=ParseMode.HTML` for formatted messages
- Async: All handlers must be `async def`

## Environment Variables

```bash
# Required
TELEGRAM_BOT_TOKEN=xxx
ALLOWED_TELEGRAM_USER_IDS=123,456

# Optional
OPENAI_API_KEY=xxx              # For voice transcription
DROID_PROJECT_SHORTCUTS='{"name":"~/path"}'
DROID_AUTO_GIT_PULL=true
DROID_AUTO_GIT_PUSH=false
DROID_DEFAULT_AUTONOMY=high
DROID_DEFAULT_MODEL=opus
DROID_DEFAULT_SYNC=true
```
</coding_guidelines>
