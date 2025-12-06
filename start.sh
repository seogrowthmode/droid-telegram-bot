#!/bin/bash
cd "$(dirname "$0")"

LOCKFILE="/tmp/droid-telegram-bot.lock"

# Check if another instance is running
if [ -f "$LOCKFILE" ]; then
    OLD_PID=$(cat "$LOCKFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Killing existing bot (PID: $OLD_PID)..."
        kill -9 "$OLD_PID" 2>/dev/null
        sleep 2
    fi
    rm -f "$LOCKFILE"
fi

# Also kill any stray bot processes
pkill -9 -f "python.*bot.py" 2>/dev/null
sleep 2

# Create lock file with our PID
echo $$ > "$LOCKFILE"

# Load all env vars except DROID_PROJECT_SHORTCUTS (has JSON that needs special handling)
export TELEGRAM_BOT_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' .env | cut -d'=' -f2-)
export TELEGRAM_ALLOWED_USER_IDS=$(grep '^TELEGRAM_ALLOWED_USER_IDS=' .env | cut -d'=' -f2-)
export DROID_DEFAULT_CWD=$(grep '^DROID_DEFAULT_CWD=' .env | cut -d'=' -f2-)
export FACTORY_API_KEY=$(grep '^FACTORY_API_KEY=' .env | cut -d'=' -f2-)
export DROID_LOG_FILE=$(grep '^DROID_LOG_FILE=' .env | cut -d'=' -f2-)
export DROID_SESSIONS_FILE=$(grep '^DROID_SESSIONS_FILE=' .env | cut -d'=' -f2-)
export DROID_AUTO_GIT_PULL=$(grep '^DROID_AUTO_GIT_PULL=' .env | cut -d'=' -f2-)
export DROID_AUTO_GIT_PUSH=$(grep '^DROID_AUTO_GIT_PUSH=' .env | cut -d'=' -f2-)

# Handle JSON env var specially
export DROID_PROJECT_SHORTCUTS='{"chadix":"~/dev/chadix-app-website"}'

# Default project settings (high autonomy, opus model, sync on)
export DROID_DEFAULT_AUTONOMY="high"
export DROID_DEFAULT_MODEL="opus"
export DROID_DEFAULT_SYNC="true"

# CLI backend: "droid" or "claude"
export DROID_CLI_TYPE="claude"
export DROID_PATH="claude"

# Run bot and save PID to lock file
python3 bot.py &
BOT_PID=$!
echo $BOT_PID > "$LOCKFILE"

# Cleanup on exit
trap "kill $BOT_PID 2>/dev/null; rm -f $LOCKFILE" EXIT INT TERM

# Wait for bot to finish
wait $BOT_PID
