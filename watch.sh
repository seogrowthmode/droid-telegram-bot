#!/bin/bash
# Auto-restart bot when bot.py, .env, or start.sh changes
cd "$(dirname "$0")"

# Kill ALL existing bot instances immediately on startup
echo "Killing any existing bot instances..."
pkill -9 -f "python.*bot.py" 2>/dev/null
sleep 1

BOT_PID=""
WATCH_FILES="bot.py .env start.sh"

cleanup() {
    echo "Stopping bot..."
    [ -n "$BOT_PID" ] && kill -9 $BOT_PID 2>/dev/null
    # Kill any orphaned bot.py processes
    pkill -9 -f "python.*bot.py" 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

get_checksum() {
    cat $WATCH_FILES 2>/dev/null | md5
}

start_bot() {
    # Kill any existing bot processes first
    pkill -9 -f "python.*bot.py" 2>/dev/null
    sleep 1
    echo "[$(date '+%H:%M:%S')] Starting bot..."
    ./start.sh &
    BOT_PID=$!
}

LAST_CHECKSUM=$(get_checksum)
start_bot

echo "Watching for changes... (Ctrl+C to stop)"
while true; do
    sleep 2
    CURRENT_CHECKSUM=$(get_checksum)
    if [ "$CURRENT_CHECKSUM" != "$LAST_CHECKSUM" ]; then
        echo "[$(date '+%H:%M:%S')] Change detected, restarting..."
        [ -n "$BOT_PID" ] && kill -9 $BOT_PID 2>/dev/null
        pkill -9 -f "python.*bot.py" 2>/dev/null
        sleep 1
        start_bot
        LAST_CHECKSUM=$CURRENT_CHECKSUM
    fi
done
