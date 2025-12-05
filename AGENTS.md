# Droid Telegram Bot - Development Guidelines

## Mandatory Rules

### 1. README Updates
**ALWAYS update README.md when:**
- Adding new commands or features
- Changing command syntax or behavior
- Adding new environment variables
- Modifying model shortcuts or autonomy levels

The pre-commit hook runs `scripts/update-readme.py` automatically, but you must also:
- Update the "Enhanced Commands" table manually for new features
- Update "Model Shortcuts" table if models change
- Update "Environment Variables" table for new config options

### 2. Git Workflow
- Always commit with descriptive messages
- Include `Co-authored-by: factory-droid[bot]` in commits
- Push changes after completing features
- Run syntax check before committing: `python3 -m py_compile bot.py`

### 3. Code Style
- Keep functions focused and single-purpose
- Add section headers with `# ===` separators for major features
- Log important events with `logger.info()` or `logger.error()`
- Handle exceptions gracefully with user-friendly messages

### 4. Testing
- Test new features in Telegram before pushing
- Verify bot restarts cleanly after changes
- Check `bot.log` for errors after testing

## Project Structure

```
bot.py              - Main bot code
start.sh            - Startup script with env loading
watch.sh            - Auto-restart on file changes
.env                - Environment config (DO NOT COMMIT)
sessions.json       - Session persistence (DO NOT COMMIT)
scripts/            - Helper scripts
  update-readme.py  - Auto-update README commands
  setup-hooks.sh    - Install git hooks
```

## Key Features to Maintain

1. **Project Shortcuts** (`/proj`) - Quick project switching with autonomy/model
2. **Git Sync** - Auto pull/push functionality
3. **Voice Messages** - Whisper transcription (requires ffmpeg)
4. **Model Selection** - Support all Droid CLI models
5. **Session Management** - Persistent sessions with settings

## Adding New Commands

1. Add the async handler function
2. Register in `main()` with `CommandHandler`
3. Update `scripts/update-readme.py` COMMAND_DOCS dict
4. Update README.md manually for detailed docs
5. Test in Telegram
6. Commit and push
