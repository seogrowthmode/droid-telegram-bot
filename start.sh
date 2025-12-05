#!/bin/bash
cd "$(dirname "$0")"

# Kill any existing bot instances to avoid Telegram conflicts
pkill -9 -f "python.*bot.py" 2>/dev/null
sleep 1

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

python3 bot.py
