#!/usr/bin/env python3
"""
Droid Telegram Bot - Enhanced Edition

A Telegram interface for Factory's Droid CLI with:
- Auto git sync (pull before tasks, commit/push after)
- Project shortcuts (/proj command)
- Voice message support

Original by: Ben Tossell (factory-ben)
Enhanced by: seogrowthmode

Repository: https://github.com/seogrowthmode/droid-telegram-bot
License: MIT
"""
import subprocess
import logging
import os
import json
import uuid
import re
import html
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
from datetime import datetime

# Configuration from environment variables
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")
LOG_FILE = os.environ.get("DROID_LOG_FILE", "/var/log/droid-telegram/bot.log")
SESSIONS_FILE = os.environ.get("DROID_SESSIONS_FILE", "/var/lib/droid-telegram/sessions.json")
DROID_PATH = os.environ.get("DROID_PATH", "droid")
DEFAULT_CWD = os.environ.get("DROID_DEFAULT_CWD", os.path.expanduser("~"))

# =============================================================================
# NEW FEATURE: Project Shortcuts
# =============================================================================
# Format: "shortcut": "full/path"
# Configure via DROID_PROJECT_SHORTCUTS env var as JSON, or edit here
DEFAULT_PROJECT_SHORTCUTS = {
    # Add your shortcuts here, e.g.:
    # "chadix": "~/dev/chadix-app-website",
    # "myapp": "~/dev/my-other-app",
}

def load_project_shortcuts():
    """Load project shortcuts from environment or use defaults"""
    shortcuts_json = os.environ.get("DROID_PROJECT_SHORTCUTS", "")
    if shortcuts_json:
        try:
            return json.loads(shortcuts_json)
        except json.JSONDecodeError:
            pass
    return DEFAULT_PROJECT_SHORTCUTS

PROJECT_SHORTCUTS = load_project_shortcuts()

# =============================================================================
# NEW FEATURE: Auto Git Sync Settings
# =============================================================================
AUTO_GIT_PULL = os.environ.get("DROID_AUTO_GIT_PULL", "true").lower() == "true"
AUTO_GIT_PUSH = os.environ.get("DROID_AUTO_GIT_PUSH", "false").lower() == "true"  # Off by default for safety

# =============================================================================
# NEW FEATURE: Model Shortcuts
# =============================================================================
MODEL_SHORTCUTS = {
    "opus": "claude-opus-4-5-20251101",
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku": "claude-haiku-4-5-20251001",
    "opus4.1": "claude-opus-4-1-20250805",
    "gpt": "gpt-5.1",
    "codex": "gpt-5.1-codex",
    "gemini": "gemini-3-pro-preview",
    "glm": "glm-4.6",
}
DEFAULT_MODEL = "opus"
AUTONOMY_LEVELS = ["off", "low", "medium", "high", "unsafe"]

def get_available_models():
    """Fetch available models from droid CLI"""
    try:
        result = subprocess.run(
            [DROID_PATH, "exec", "--help"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            # Parse models from help output
            models = {}
            in_models = False
            for line in result.stdout.split('\n'):
                if 'Available Models:' in line:
                    in_models = True
                    continue
                if in_models:
                    if line.strip() and not line.startswith(' '):
                        break
                    match = re.match(r'\s+(\S+)\s+(.+)', line)
                    if match:
                        model_id, name = match.groups()
                        models[model_id] = name.strip()
            return models if models else None
    except:
        pass
    return None

def resolve_model(shortcut):
    """Resolve model shortcut to full model ID"""
    if not shortcut:
        return MODEL_SHORTCUTS.get(DEFAULT_MODEL)
    shortcut = shortcut.lower()
    if shortcut in MODEL_SHORTCUTS:
        return MODEL_SHORTCUTS[shortcut]
    # Check if it's already a full model ID
    if "-" in shortcut or "." in shortcut:
        return shortcut
    return None

# Validate required config
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

# Parse allowed user IDs
def parse_allowed_users():
    if not ALLOWED_USER_IDS:
        return set()
    try:
        return {int(uid.strip()) for uid in ALLOWED_USER_IDS.split(",") if uid.strip()}
    except ValueError as e:
        raise ValueError(f"Invalid TELEGRAM_ALLOWED_USER_IDS format: {e}")

ALLOWED_USERS = parse_allowed_users()

def is_authorized(user_id):
    if not ALLOWED_USERS:
        return False
    return user_id in ALLOWED_USERS

# Ensure directories exist
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)

# State
streaming_mode = True
sessions = {}
session_headers = {}
active_session_per_user = {}
pending_permissions = {}
session_history = []
session_autonomy = {}
active_processes = {}
session_git_sync = {}  # Track git sync settings per session
session_models = {}  # Track model per session

BOT_CONTEXT = """[Telegram Bot Context: You're running inside a Telegram bot. The user can use /new <path> to change the working directory for their session (e.g., /new ~/projects/myapp). Don't suggest using cd to change directories - instead tell them to use /new <path>. They can also use /proj <shortcut> for quick project switching.]

"""

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =============================================================================
# GIT HELPER FUNCTIONS
# =============================================================================

def is_git_repo(cwd):
    """Check if directory is a git repository"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except:
        return False

def git_pull(cwd):
    """Pull latest changes from remote"""
    try:
        result = subprocess.run(
            ["git", "pull", "--rebase"],
            cwd=cwd, capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return True, result.stdout.strip() or "Already up to date"
        else:
            return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Git pull timed out"
    except Exception as e:
        return False, str(e)

def git_has_changes(cwd):
    """Check if there are uncommitted changes"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd, capture_output=True, text=True, timeout=5
        )
        return bool(result.stdout.strip())
    except:
        return False

def git_commit_and_push(cwd, message="Auto-commit from Telegram Droid bot"):
    """Stage all changes, commit, and push"""
    try:
        # Stage all changes
        subprocess.run(["git", "add", "-A"], cwd=cwd, timeout=10)
        
        # Commit
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=cwd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            if "nothing to commit" in result.stdout.lower() or "nothing to commit" in result.stderr.lower():
                return True, "Nothing to commit"
            return False, result.stderr.strip()
        
        # Push
        push_result = subprocess.run(
            ["git", "push"],
            cwd=cwd, capture_output=True, text=True, timeout=60
        )
        if push_result.returncode == 0:
            return True, "Changes committed and pushed"
        else:
            return False, f"Committed but push failed: {push_result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "Git operation timed out"
    except Exception as e:
        return False, str(e)

def get_git_status(cwd):
    """Get git status summary for a directory"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None, "Not a git repo"

        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd, capture_output=True, text=True, timeout=5
        )
        branch = branch_result.stdout.strip() or "detached HEAD"

        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd, capture_output=True, text=True, timeout=5
        )
        changes = status_result.stdout.strip().split('\n') if status_result.stdout.strip() else []
        num_changes = len(changes)

        if num_changes == 0:
            return "clean", f"on {branch} (clean)"
        else:
            return "dirty", f"on {branch} ({num_changes} uncommitted)"
    except Exception as e:
        return None, f"git error: {e}"

# =============================================================================
# SESSION MANAGEMENT
# =============================================================================

def load_sessions():
    global sessions, active_session_per_user, session_history, session_autonomy, session_git_sync, session_models
    try:
        if os.path.exists(SESSIONS_FILE):
            with open(SESSIONS_FILE, 'r') as f:
                data = json.load(f)
                sessions = {int(k): v for k, v in data.get("sessions", {}).items()}
                active_session_per_user = {int(k): v for k, v in data.get("active_session_per_user", {}).items()}
                session_history = data.get("session_history", [])
                session_autonomy = data.get("session_autonomy", {})
                session_git_sync = data.get("session_git_sync", {})
                session_models = data.get("session_models", {})
                logger.info(f"Loaded {len(sessions)} sessions")
    except Exception as e:
        logger.error(f"Failed to load sessions: {e}")

def save_sessions():
    try:
        data = {
            "sessions": {str(k): v for k, v in sessions.items()},
            "active_session_per_user": {str(k): v for k, v in active_session_per_user.items()},
            "session_history": session_history[-100:],
            "session_autonomy": session_autonomy,
            "session_git_sync": session_git_sync,
            "session_models": session_models
        }
        with open(SESSIONS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save sessions: {e}")

def add_to_session_history(session_id, cwd, first_message=None):
    if not session_id:
        return
    for entry in session_history:
        if entry.get("session_id") == session_id:
            return
    session_history.append({
        "session_id": session_id,
        "cwd": cwd,
        "started": datetime.now().isoformat(),
        "first_message": (first_message[:50] + "...") if first_message and len(first_message) > 50 else first_message
    })
    save_sessions()

# =============================================================================
# TEXT FORMATTING
# =============================================================================

def markdown_to_html(text):
    if not text:
        return text
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"§§CODEBLOCK{len(code_blocks)-1}§§"
    text = re.sub(r'```(\w*)\n(.*?)```', save_code_block, text, flags=re.DOTALL)
    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(1))
        return f"§§INLINECODE{len(inline_codes)-1}§§"
    text = re.sub(r'(?<!`)`([^`]+)`(?!`)', save_inline_code, text)
    text = html.escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'<i>\1</i>', text)
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'^[\-\*]\s+', '• ', text, flags=re.MULTILINE)
    for i, code in enumerate(inline_codes):
        escaped_code = html.escape(code)
        text = text.replace(f"§§INLINECODE{i}§§", f"<code>{escaped_code}</code>")
    for i, block in enumerate(code_blocks):
        match = re.match(r'```(\w*)\n(.*?)```', block, re.DOTALL)
        if match:
            code = match.group(2)
            escaped_code = html.escape(code.strip())
            text = text.replace(f"§§CODEBLOCK{i}§§", f"<pre>{escaped_code}</pre>")
        else:
            text = text.replace(f"§§CODEBLOCK{i}§§", block)
    return text

async def send_formatted_message(message, text):
    try:
        html_text = markdown_to_html(text)
        return await message.reply_text(html_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"HTML parsing failed, sending plain text: {e}")
        return await message.reply_text(text)

# =============================================================================
# COMMANDS
# =============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized. Contact the bot administrator.")
        return
    
    shortcuts_list = "\n".join([f"  • {k}" for k in PROJECT_SHORTCUTS.keys()]) if PROJECT_SHORTCUTS else "  (none configured)"
    
    await update.message.reply_text(
        "🤖 <b>Droid Telegram Bot - Enhanced</b>\n\n"
        "<b>Commands:</b>\n"
        "/new [path] - Start new session\n"
        "/proj [shortcut] - Quick project switch\n"
        "/sync - Toggle auto git sync\n"
        "/push - Commit and push changes\n"
        "/pull - Pull latest changes\n"
        "/session - List/switch sessions\n"
        "/auto [level] - Set autonomy level\n"
        "/status - Bot status\n"
        "/help - Detailed help\n\n"
        f"<b>Project Shortcuts:</b>\n{shortcuts_list}\n\n"
        "💡 Send voice messages to code hands-free!",
        parse_mode=ParseMode.HTML
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    
    shortcuts_list = "\n".join([f"  <code>{k}</code> → {v}" for k, v in PROJECT_SHORTCUTS.items()]) if PROJECT_SHORTCUTS else "  (none configured)"
    
    await update.message.reply_text(
        "🤖 <b>Droid Telegram Bot - Enhanced Edition</b>\n\n"
        "<b>🆕 New Features:</b>\n"
        "• <b>Voice Messages</b> - Send voice notes, they get transcribed and sent to Droid\n"
        "• <b>Project Shortcuts</b> - /proj chadix instead of typing full paths\n"
        "• <b>Auto Git Sync</b> - Auto pull before tasks, push after\n\n"
        "<b>⚙️ Commands:</b>\n"
        "/new [path] - New session in directory\n"
        "/proj [shortcut] - Switch to project by shortcut\n"
        "/sync - Toggle auto git pull/push\n"
        "/pull - Manually pull latest\n"
        "/push [msg] - Commit and push with message\n"
        "/session - List/switch sessions\n"
        "/auto [level] - Set autonomy (off/low/medium/high/unsafe)\n"
        "/cwd - Show current directory\n"
        "/git [cmd] - Run git commands\n"
        "/stop - Stop running task\n"
        "/status - Bot status\n\n"
        f"<b>📁 Project Shortcuts:</b>\n{shortcuts_list}\n\n"
        "<b>💡 Tips:</b>\n"
        "• Reply to any message to continue that session\n"
        "• Use /auto high to enable tool execution\n"
        "• Voice messages work great for quick ideas!",
        parse_mode=ParseMode.HTML
    )

# =============================================================================
# NEW COMMAND: Project Shortcuts
# =============================================================================

async def proj_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick project switching with shortcuts - supports /proj <shortcut> [autonomy] [model]"""
    if not is_authorized(update.effective_user.id):
        return
    
    args = update.message.text.split()[1:] if len(update.message.text.split()) > 1 else []
    
    if not args:
        # List available shortcuts with usage info
        model_list = ", ".join(MODEL_SHORTCUTS.keys())
        if not PROJECT_SHORTCUTS:
            await update.message.reply_text(
                "No project shortcuts configured.\n\n"
                "Add them to your .env file:\n"
                "<code>DROID_PROJECT_SHORTCUTS='{\"myapp\": \"~/dev/myapp\"}'</code>",
                parse_mode=ParseMode.HTML
            )
        else:
            lines = ["<b>📁 Project Shortcuts</b>\n"]
            for shortcut, path in PROJECT_SHORTCUTS.items():
                lines.append(f"<code>/proj {shortcut}</code> → {path}")
            lines.append(f"\n<b>Usage:</b> <code>/proj shortcut [auto] [model]</code>")
            lines.append(f"<b>Autonomy:</b> off, low, medium, high, unsafe")
            lines.append(f"<b>Models:</b> {model_list}")
            lines.append(f"\n<b>Example:</b> <code>/proj chadix high sonnet</code>")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return
    
    # Parse arguments: /proj <shortcut> [autonomy] [model]
    shortcut = args[0].lower()
    autonomy_level = "off"
    model_shortcut = None
    
    for arg in args[1:]:
        arg_lower = arg.lower()
        if arg_lower in AUTONOMY_LEVELS:
            autonomy_level = arg_lower
        elif arg_lower in MODEL_SHORTCUTS or resolve_model(arg_lower):
            model_shortcut = arg_lower
    
    if shortcut not in PROJECT_SHORTCUTS:
        available = ", ".join(PROJECT_SHORTCUTS.keys()) if PROJECT_SHORTCUTS else "none"
        await update.message.reply_text(f"❌ Unknown shortcut: {shortcut}\n\nAvailable: {available}")
        return
    
    # Resolve the path
    path = PROJECT_SHORTCUTS[shortcut]
    resolved_cwd = os.path.expanduser(path)
    
    if not os.path.isdir(resolved_cwd):
        await update.message.reply_text(f"❌ Directory not found: {path}")
        return
    
    user_id = update.effective_user.id
    
    # Auto git pull if enabled
    git_msg = ""
    if AUTO_GIT_PULL and is_git_repo(resolved_cwd):
        success, pull_msg = git_pull(resolved_cwd)
        if success:
            git_msg = f"\n🔄 {pull_msg}"
        else:
            git_msg = f"\n⚠️ Pull failed: {pull_msg}"
    
    # Create session with ID immediately (so /auto works)
    temp_session_id = f"tg-{str(uuid.uuid4())[:8]}"
    short_cwd = resolved_cwd.replace(os.path.expanduser("~"), "~")
    git_state, git_info = get_git_status(resolved_cwd)
    
    # Resolve model
    model_id = resolve_model(model_shortcut) if model_shortcut else None
    model_display = model_shortcut or "default"
    
    # Set autonomy and model for this session
    session_autonomy[temp_session_id] = autonomy_level
    if model_id:
        session_models[temp_session_id] = model_id
    
    # Build status display
    auto_emoji = {"off": "👁", "low": "🔒", "medium": "🔓", "high": "⚡", "unsafe": "⚠️"}
    status_lines = [
        f"📂 {short_cwd}",
        f"🆔 {temp_session_id}",
        f"🌿 {git_info}{git_msg}",
        f"{auto_emoji.get(autonomy_level, '')} Auto: {autonomy_level}",
    ]
    if model_id:
        status_lines.append(f"🤖 Model: {model_display}")
    
    header_text = "\n".join(status_lines)
    header_msg = await update.message.reply_text(header_text)
    
    session_data = {
        "session_id": temp_session_id,
        "cwd": resolved_cwd,
        "header_msg_id": header_msg.message_id,
        "awaiting_first_message": True
    }
    sessions[header_msg.message_id] = session_data
    
    active_session_per_user[user_id] = {
        "session_id": temp_session_id,
        "cwd": resolved_cwd,
        "last_msg_id": header_msg.message_id
    }
    save_sessions()
    
    # Quick action buttons for phone users
    keyboard = [
        [
            InlineKeyboardButton("⚡ High", callback_data=f"setauto_{temp_session_id}_high"),
            InlineKeyboardButton("🔓 Med", callback_data=f"setauto_{temp_session_id}_medium"),
            InlineKeyboardButton("👁 Off", callback_data=f"setauto_{temp_session_id}_off"),
        ],
        [
            InlineKeyboardButton("🎭 Opus", callback_data=f"setmodel_{temp_session_id}_opus"),
            InlineKeyboardButton("🎵 Sonnet", callback_data=f"setmodel_{temp_session_id}_sonnet"),
            InlineKeyboardButton("💨 Haiku", callback_data=f"setmodel_{temp_session_id}_haiku"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"✓ Ready! Send your task or tap to adjust:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button callbacks for settings"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    
    if data.startswith("setauto_"):
        parts = data.split("_")
        if len(parts) >= 3:
            session_id = parts[1]
            level = parts[2]
            session_autonomy[session_id] = level
            save_sessions()
            emoji = {"off": "👁", "low": "🔒", "medium": "🔓", "high": "⚡", "unsafe": "⚠️"}
            await query.edit_message_text(f"{emoji.get(level, '')} Autonomy: <b>{level}</b>\n\nSend your task!", parse_mode=ParseMode.HTML)
    
    elif data.startswith("setmodel_"):
        parts = data.split("_")
        if len(parts) >= 3:
            session_id = parts[1]
            model_short = parts[2]
            model_id = resolve_model(model_short)
            if model_id:
                session_models[session_id] = model_id
                save_sessions()
                await query.edit_message_text(f"🤖 Model: <b>{model_short}</b>\n\nSend your task!", parse_mode=ParseMode.HTML)

# =============================================================================
# NEW COMMAND: Git Sync Controls
# =============================================================================

async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle auto git sync for current session"""
    if not is_authorized(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    session_id = active_session_per_user.get(user_id, {}).get("session_id")
    
    if not session_id:
        await update.message.reply_text("No active session. Use /new or /proj first.")
        return
    
    current = session_git_sync.get(session_id, {"pull": AUTO_GIT_PULL, "push": AUTO_GIT_PUSH})
    
    args = update.message.text.split()[1:] if len(update.message.text.split()) > 1 else []
    
    if not args:
        pull_status = "✓ ON" if current.get("pull", AUTO_GIT_PULL) else "✗ OFF"
        push_status = "✓ ON" if current.get("push", AUTO_GIT_PUSH) else "✗ OFF"
        await update.message.reply_text(
            f"<b>Git Sync Settings</b>\n\n"
            f"Auto Pull: {pull_status}\n"
            f"Auto Push: {push_status}\n\n"
            f"Usage:\n"
            f"/sync pull - Toggle auto pull\n"
            f"/sync push - Toggle auto push\n"
            f"/sync on - Enable both\n"
            f"/sync off - Disable both",
            parse_mode=ParseMode.HTML
        )
        return
    
    action = args[0].lower()
    
    if action == "pull":
        current["pull"] = not current.get("pull", AUTO_GIT_PULL)
        status = "enabled" if current["pull"] else "disabled"
        msg = f"Auto git pull {status}"
    elif action == "push":
        current["push"] = not current.get("push", AUTO_GIT_PUSH)
        status = "enabled" if current["push"] else "disabled"
        msg = f"Auto git push {status}"
    elif action == "on":
        current["pull"] = True
        current["push"] = True
        msg = "Auto git sync enabled (pull + push)"
    elif action == "off":
        current["pull"] = False
        current["push"] = False
        msg = "Auto git sync disabled"
    else:
        await update.message.reply_text("Usage: /sync [pull|push|on|off]")
        return
    
    session_git_sync[session_id] = current
    save_sessions()
    await update.message.reply_text(f"✓ {msg}")

async def pull_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually pull latest changes"""
    if not is_authorized(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    cwd = active_session_per_user.get(user_id, {}).get("cwd", DEFAULT_CWD)
    
    if not is_git_repo(cwd):
        await update.message.reply_text("❌ Not a git repository")
        return
    
    status_msg = await update.message.reply_text("🔄 Pulling...")
    
    success, msg = git_pull(cwd)
    if success:
        await status_msg.edit_text(f"✓ {msg}")
    else:
        await status_msg.edit_text(f"❌ Pull failed: {msg}")

async def push_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commit and push changes"""
    if not is_authorized(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    cwd = active_session_per_user.get(user_id, {}).get("cwd", DEFAULT_CWD)
    
    if not is_git_repo(cwd):
        await update.message.reply_text("❌ Not a git repository")
        return
    
    if not git_has_changes(cwd):
        await update.message.reply_text("✓ Nothing to commit")
        return
    
    # Get custom commit message if provided
    args = update.message.text[5:].strip()  # Remove "/push"
    commit_msg = args if args else "Auto-commit from Telegram Droid bot"
    
    status_msg = await update.message.reply_text("📤 Committing and pushing...")
    
    success, msg = git_commit_and_push(cwd, commit_msg)
    if success:
        await status_msg.edit_text(f"✓ {msg}")
    else:
        await status_msg.edit_text(f"❌ {msg}")

# =============================================================================
# NEW FEATURE: Voice Message Support
# =============================================================================

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages - transcribe and send to Droid"""
    if not is_authorized(update.effective_user.id):
        return
    
    status_msg = await update.message.reply_text("🎤 Transcribing voice message...")
    
    try:
        # Download voice file
        voice = update.message.voice
        file = await context.bot.get_file(voice.file_id)
        
        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            await file.download_to_drive(tmp.name)
            tmp_path = tmp.name
        
        # Transcribe using whisper Python module
        transcribed_text = None
        try:
            import whisper
            model = whisper.load_model("base")
            result = model.transcribe(tmp_path)
            transcribed_text = result.get("text", "").strip()
        except ImportError:
            # Whisper not installed, try CLI as fallback
            whisper_paths = [
                "whisper",
                "/Library/Frameworks/Python.framework/Versions/3.10/bin/whisper",
                "/usr/local/bin/whisper",
                os.path.expanduser("~/.local/bin/whisper"),
            ]
            for whisper_cmd in whisper_paths:
                try:
                    result = subprocess.run(
                        [whisper_cmd, tmp_path, "--model", "base", "--output_format", "txt", "--output_dir", "/tmp"],
                        capture_output=True, text=True, timeout=120
                    )
                    txt_file = tmp_path.replace(".ogg", ".txt")
                    if os.path.exists(txt_file):
                        with open(txt_file, 'r') as f:
                            transcribed_text = f.read().strip()
                        os.remove(txt_file)
                        break
                except:
                    continue
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
        
        # Clean up temp file
        os.remove(tmp_path)
        
        if not transcribed_text:
            await status_msg.edit_text(
                "❌ Could not transcribe voice message.\n\n"
                "To enable voice messages, install Whisper:\n"
                "<code>pip install openai-whisper</code>",
                parse_mode=ParseMode.HTML
            )
            return
        
        await status_msg.edit_text(f"🎤 Transcribed: \"{transcribed_text}\"\n\nProcessing...")
        
        # Now process as a regular message
        update.message.text = transcribed_text
        await handle_message(update, context)
        
    except Exception as e:
        logger.error(f"Voice message error: {e}")
        await status_msg.edit_text(f"❌ Error processing voice message: {str(e)}")

# =============================================================================
# ORIGINAL COMMANDS (with git sync integration)
# =============================================================================

def resolve_cwd(path_arg):
    if not path_arg:
        return DEFAULT_CWD
    if path_arg.startswith("/"):
        resolved = path_arg
    elif path_arg.startswith("~"):
        resolved = os.path.expanduser(path_arg)
    else:
        resolved = os.path.join(DEFAULT_CWD, path_arg)
    if os.path.isdir(resolved):
        return resolved
    else:
        return None

async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return

    message_text = update.message.text
    arg = message_text[4:].strip() if len(message_text) > 4 else ""
    
    resolved_cwd = resolve_cwd(arg) if arg else DEFAULT_CWD

    if arg and resolved_cwd is None:
        if "/" in arg or "~" in arg:
            await update.message.reply_text(f"❌ Directory not found: {arg}")
            return
        resolved_cwd = DEFAULT_CWD
        prompt = arg
    elif arg and resolved_cwd:
        prompt = None
    else:
        resolved_cwd = DEFAULT_CWD
        prompt = None

    user_id = update.effective_user.id
    temp_session_ref = str(uuid.uuid4())[:8]

    # Auto git pull if enabled
    git_sync_msg = ""
    if AUTO_GIT_PULL and is_git_repo(resolved_cwd):
        success, pull_msg = git_pull(resolved_cwd)
        if success:
            git_sync_msg = f"\n🔄 Pulled: {pull_msg}"
        else:
            git_sync_msg = f"\n⚠️ Pull failed: {pull_msg}"

    short_cwd = resolved_cwd.replace(os.path.expanduser("~"), "~")
    git_state, git_info = get_git_status(resolved_cwd)
    if git_state == "clean":
        git_line = f"✓ Git: {git_info}"
    elif git_state == "dirty":
        git_line = f"⚠️ Git: {git_info}"
    else:
        git_line = f"Git: {git_info}"

    header_text = f"📂 {short_cwd}\n🆔 Session: {temp_session_ref}\n{git_line}{git_sync_msg}"
    header_msg = await update.message.reply_text(header_text)

    if prompt:
        status_text = "Working..." if streaming_mode else "Thinking..."
        status_msg = await update.message.reply_text(status_text)

        try:
            if streaming_mode:
                response, session_id = await handle_message_streaming(prompt, None, status_msg, resolved_cwd, user_id=user_id)
            else:
                response, session_id = await handle_message_simple(prompt, None, resolved_cwd)

            response = response or "No response from Droid"

            if len(response) > 4000:
                response = response[:4000] + "\n\n[Response truncated]"

            await status_msg.delete()
            reply_msg = await send_formatted_message(update.message, response)

            actual_session_id = session_id or temp_session_ref
            session_data = {
                "session_id": actual_session_id,
                "cwd": resolved_cwd,
                "header_msg_id": header_msg.message_id
            }
            sessions[reply_msg.message_id] = session_data
            sessions[header_msg.message_id] = session_data

            active_session_per_user[user_id] = {
                "session_id": actual_session_id,
                "cwd": resolved_cwd,
                "last_msg_id": reply_msg.message_id
            }

            add_to_session_history(actual_session_id, resolved_cwd, prompt)
            save_sessions()

            # Auto git push if enabled and there are changes
            if AUTO_GIT_PUSH and is_git_repo(resolved_cwd) and git_has_changes(resolved_cwd):
                success, push_msg = git_commit_and_push(resolved_cwd)
                if success:
                    await update.message.reply_text(f"📤 Auto-pushed: {push_msg}")

            logger.info(f"New session started: {actual_session_id} in {resolved_cwd}")

        except subprocess.TimeoutExpired:
            await status_msg.edit_text("Request timed out (5 min limit).")
        except Exception as e:
            await status_msg.edit_text(f"Error: {str(e)}")
            logger.error(f"Error: {e}")
    else:
        session_data = {
            "session_id": None,
            "cwd": resolved_cwd,
            "header_msg_id": header_msg.message_id,
            "awaiting_first_message": True
        }
        sessions[header_msg.message_id] = session_data

        instruction_msg = await update.message.reply_text(
            f"🆕 New session started in <code>{short_cwd}</code>\n\nReply to continue.",
            parse_mode=ParseMode.HTML
        )
        sessions[instruction_msg.message_id] = session_data

        active_session_per_user[user_id] = {
            "session_id": None,
            "cwd": resolved_cwd,
            "last_msg_id": instruction_msg.message_id
        }
        save_sessions()

async def cwd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return

    user_id = update.effective_user.id
    cwd = active_session_per_user.get(user_id, {}).get("cwd", DEFAULT_CWD)
    short_cwd = cwd.replace(os.path.expanduser("~"), "~")
    git_state, git_info = get_git_status(cwd)

    await update.message.reply_text(
        f"📂 Current directory: <code>{short_cwd}</code>\n"
        f"Git: {git_info}\n\n"
        f"Use /new or /proj to change directory",
        parse_mode=ParseMode.HTML
    )

async def stream_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global streaming_mode
    if not is_authorized(update.effective_user.id):
        return
    streaming_mode = not streaming_mode
    status = "ON" if streaming_mode else "OFF"
    await update.message.reply_text(f"Live tool updates: {status}")

async def auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return

    user_id = update.effective_user.id
    args = update.message.text.split()[1:]
    session_id = active_session_per_user.get(user_id, {}).get("session_id")

    if not args:
        current = session_autonomy.get(session_id, "off") if session_id else "off"
        await update.message.reply_text(
            f"Current autonomy: <code>{current}</code>\n\n"
            f"Usage: <code>/auto [level]</code>\n"
            f"Levels: off | low | medium | high | unsafe\n\n"
            f"• off = read-only\n"
            f"• low = safe tools only\n"
            f"• medium = most tools\n"
            f"• high = all tools, asks for risky\n"
            f"• unsafe = skip all checks",
            parse_mode=ParseMode.HTML
        )
        return

    level = args[0].lower()
    valid_levels = ["off", "low", "medium", "high", "unsafe"]

    if level not in valid_levels:
        await update.message.reply_text(f"Invalid level. Use: {', '.join(valid_levels)}")
        return

    if not session_id:
        await update.message.reply_text("No active session. Start one with /new or /proj first.")
        return

    emoji = {"off": "👁", "low": "🔒", "medium": "🔓", "high": "⚡", "unsafe": "⚠️"}
    session_autonomy[session_id] = level
    save_sessions()
    await update.message.reply_text(f"{emoji.get(level, '')} Autonomy set to <code>{level}</code>", parse_mode=ParseMode.HTML)

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return

    user_id = update.effective_user.id

    if user_id not in active_processes:
        await update.message.reply_text("No active process to stop.")
        return

    proc_info = active_processes.pop(user_id)
    process = proc_info.get("process")
    status_msg = proc_info.get("status_msg")

    if process and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=2)
        except:
            process.kill()

        if status_msg:
            try:
                await status_msg.edit_text("🛑 Stopped by user")
            except:
                pass

        await update.message.reply_text("✓ Process stopped")
    else:
        await update.message.reply_text("Process already finished.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    try:
        droid_result = subprocess.run([DROID_PATH, "--version"], capture_output=True, text=True, timeout=10)
        droid_version = droid_result.stdout.strip() or "unknown"
        stream_status = "ON" if streaming_mode else "OFF"
        
        user_id = update.effective_user.id
        active_info = ""
        if user_id in active_session_per_user:
            active = active_session_per_user[user_id]
            sid = active.get("session_id", "")[:8] if active.get("session_id") else "pending"
            cwd = active.get("cwd", "").replace(os.path.expanduser("~"), "~")
            active_info = f"\n\n<b>Your session:</b> {sid}\n📂 {cwd}"

        shortcuts_count = len(PROJECT_SHORTCUTS)
        
        await update.message.reply_text(
            f"✅ <b>Bot Status: Running</b>\n\n"
            f"🤖 Droid: {droid_version}\n"
            f"⚡ Live updates: {stream_status}\n"
            f"📁 Project shortcuts: {shortcuts_count}\n"
            f"🔄 Auto pull: {'ON' if AUTO_GIT_PULL else 'OFF'}\n"
            f"📤 Auto push: {'ON' if AUTO_GIT_PUSH else 'OFF'}"
            f"{active_info}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def git_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return

    user_id = update.effective_user.id
    cwd = active_session_per_user.get(user_id, {}).get("cwd", DEFAULT_CWD)

    text = update.message.text
    git_args = text[4:].strip() if len(text) > 4 else ""

    if not git_args:
        git_state, git_info = get_git_status(cwd)
        short_cwd = cwd.replace(os.path.expanduser("~"), "~")
        await update.message.reply_text(
            f"📂 {short_cwd}\nGit: {git_info}\n\n"
            "Usage: /git <command>\n"
            "Examples: /git status, /git pull, /git log --oneline -5"
        )
        return

    short_cwd = cwd.replace(os.path.expanduser("~"), "~")
    status_msg = await update.message.reply_text(f"Running git {git_args.split()[0]}...")

    try:
        result = subprocess.run(
            f"git {git_args}",
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        if len(output) > 3500:
            output = output[:3500] + "\n\n[truncated]"

        await status_msg.edit_text(f"📂 {short_cwd}\n<pre>$ git {git_args}\n{html.escape(output)}</pre>", parse_mode=ParseMode.HTML)
    except subprocess.TimeoutExpired:
        await status_msg.edit_text("Command timed out")
    except Exception as e:
        await status_msg.edit_text(f"Error: {e}")

async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return

    user_id = update.effective_user.id
    args = update.message.text.split()[1:] if len(update.message.text.split()) > 1 else []

    if args:
        target = args[0]
        found = None
        for entry in session_history:
            if entry["session_id"].startswith(target):
                found = entry
                break

        if found:
            active_session_per_user[user_id] = {
                "session_id": found["session_id"],
                "cwd": found["cwd"],
                "last_msg_id": None
            }
            save_sessions()
            short_cwd = found["cwd"].replace(os.path.expanduser("~"), "~")
            await update.message.reply_text(
                f"✓ Switched to session <code>{found['session_id'][:8]}</code>\n"
                f"📂 {short_cwd}",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(f"Session not found: {target}")
    else:
        if not session_history:
            await update.message.reply_text("No sessions yet. Use /new or /proj to start one.")
            return

        lines = ["<b>Recent Sessions</b>\n"]
        for entry in reversed(session_history[-10:]):
            sid = entry["session_id"][:8]
            cwd = entry["cwd"].replace(os.path.expanduser("~"), "~")
            msg = entry.get("first_message", "")[:30] or "N/A"

            current = ""
            if user_id in active_session_per_user:
                if active_session_per_user[user_id].get("session_id") == entry["session_id"]:
                    current = " ✓"

            lines.append(f"<code>{sid}</code> {cwd}{current}\n  <i>{msg}</i>\n")

        lines.append("\nUse <code>/session [id]</code> to switch")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

# =============================================================================
# MESSAGE HANDLING
# =============================================================================

def format_tool_call(data):
    tool_name = data.get("toolName", "") or data.get("name", "unknown")
    params = data.get("input", {}) or data.get("parameters", {}) or data.get("args", {})
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except:
            params = {}

    detail = ""
    if tool_name in ["Read", "Edit", "MultiEdit", "Create"]:
        file_path = params.get("file_path", "") or params.get("path", "")
        if file_path:
            detail = "..." + file_path[-47:] if len(file_path) > 50 else file_path
    elif tool_name == "Grep":
        pattern = params.get("pattern", "")
        if pattern:
            detail = f"'{pattern[:20]}...'" if len(pattern) > 20 else f"'{pattern}'"
    elif tool_name == "Execute":
        cmd = params.get("command", "")
        if cmd:
            detail = cmd[:40] + "..." if len(cmd) > 40 else cmd

    return f"→ {tool_name}: {detail}" if detail else f"→ {tool_name}"

def extract_final_text(line):
    if '"finalText"' not in line:
        return None
    try:
        data = json.loads(line)
        return data.get("finalText", "")
    except:
        pass
    return None

def extract_session_id(line):
    try:
        data = json.loads(line)
        return data.get("session_id")
    except:
        pass
    return None

async def handle_message_streaming(user_message, session_id, status_msg, cwd=None, autonomy_level="off", user_id=None, model=None):
    env = os.environ.copy()
    working_dir = cwd or DEFAULT_CWD

    cmd = [DROID_PATH, "exec"]
    if autonomy_level != "off":
        cmd.extend(["--auto", autonomy_level])
    if model:
        cmd.extend(["-m", model])
    cmd.extend(["--output-format", "stream-json"])
    if session_id:
        cmd.extend(["-s", session_id])

    message_with_context = BOT_CONTEXT + user_message if not session_id else user_message
    cmd.append(message_with_context)

    logger.info(f"Running droid in cwd: {working_dir}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=working_dir,
        bufsize=1
    )

    if user_id:
        active_processes[user_id] = {"process": process, "status_msg": status_msg}

    last_update = ""
    final_response = ""
    new_session_id = None
    tool_updates = []

    while True:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            continue

        line = line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
            event_type = data.get("type", "")

            if event_type == "tool_call":
                tool_display = format_tool_call(data)
                tool_updates.append(tool_display)
                display_tools = tool_updates[-5:]
                new_status = "Working...\n\n" + "\n".join(display_tools)
                if new_status != last_update:
                    try:
                        await status_msg.edit_text(new_status)
                        last_update = new_status
                    except:
                        pass

            elif event_type == "completion":
                final_response = data.get("finalText", "")
                new_session_id = data.get("session_id")

            elif event_type == "error":
                final_response = f"Error: {data.get('message', 'Unknown error')}"

        except json.JSONDecodeError:
            extracted = extract_final_text(line)
            if extracted:
                final_response = extracted
            if not new_session_id:
                new_session_id = extract_session_id(line)

    stderr = process.stderr.read()
    if stderr and not final_response:
        final_response = stderr.strip()

    process.wait()

    if user_id and user_id in active_processes:
        del active_processes[user_id]

    return final_response.strip(), new_session_id

async def handle_message_simple(user_message, session_id, cwd=None, autonomy_level="off", model=None):
    env = os.environ.copy()
    working_dir = cwd or DEFAULT_CWD

    cmd = [DROID_PATH, "exec"]
    if autonomy_level != "off":
        cmd.extend(["--auto", autonomy_level])
    if model:
        cmd.extend(["-m", model])
    if session_id:
        cmd.extend(["-s", session_id])

    message_with_context = BOT_CONTEXT + user_message if not session_id else user_message
    cmd.append(message_with_context)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
        cwd=working_dir
    )

    return result.stdout.strip() or result.stderr.strip(), None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    user_message = update.message.text
    logger.info(f"Received message: {user_message[:100]}...")

    session_id = None
    session_cwd = DEFAULT_CWD
    session_data = None
    is_continuation = False

    if update.message.reply_to_message:
        replied_msg_id = update.message.reply_to_message.message_id
        if replied_msg_id in sessions:
            session_data = sessions[replied_msg_id]
            if isinstance(session_data, dict):
                session_id = session_data.get("session_id")
                session_cwd = session_data.get("cwd") or DEFAULT_CWD
            else:
                session_id = session_data
            is_continuation = True
    
    if not is_continuation and user_id in active_session_per_user:
        active = active_session_per_user[user_id]
        session_id = active.get("session_id")
        session_cwd = active.get("cwd") or DEFAULT_CWD
        is_continuation = bool(session_id)

    # Auto git pull before task
    sync_settings = session_git_sync.get(session_id, {"pull": AUTO_GIT_PULL, "push": AUTO_GIT_PUSH})
    git_pull_msg = ""
    if sync_settings.get("pull", AUTO_GIT_PULL) and is_git_repo(session_cwd):
        success, msg = git_pull(session_cwd)
        if success and "Already up to date" not in msg:
            git_pull_msg = f"\n🔄 Pulled: {msg}"

    short_cwd = session_cwd.replace(os.path.expanduser("~"), "~")
    autonomy_level = session_autonomy.get(session_id, "off") if session_id else "off"
    model = session_models.get(session_id) if session_id else None

    status_text = f"Working in {short_cwd}"
    if git_pull_msg:
        status_text += git_pull_msg
    status_msg = await update.message.reply_text(status_text)

    try:
        if streaming_mode:
            response, new_session_id = await handle_message_streaming(user_message, session_id, status_msg, session_cwd, autonomy_level, user_id=user_id, model=model)
        else:
            response, new_session_id = await handle_message_simple(user_message, session_id, session_cwd, autonomy_level, model=model)

        response = response or "No response from Droid"

        if len(response) > 4000:
            response = response[:4000] + "\n\n[Response truncated]"

        await status_msg.delete()
        reply_msg = await send_formatted_message(update.message, response)

        actual_session_id = new_session_id or session_id
        new_session_data = {
            "session_id": actual_session_id,
            "cwd": session_cwd,
            "header_msg_id": session_data.get("header_msg_id") if isinstance(session_data, dict) else None
        }
        sessions[reply_msg.message_id] = new_session_data

        active_session_per_user[user_id] = {
            "session_id": actual_session_id,
            "cwd": session_cwd,
            "last_msg_id": reply_msg.message_id
        }

        if actual_session_id and not is_continuation:
            add_to_session_history(actual_session_id, session_cwd, user_message)

        save_sessions()

        # Auto git push after task
        if sync_settings.get("push", AUTO_GIT_PUSH) and is_git_repo(session_cwd) and git_has_changes(session_cwd):
            success, push_msg = git_commit_and_push(session_cwd)
            if success:
                await update.message.reply_text(f"📤 Auto-pushed: {push_msg}")
            else:
                await update.message.reply_text(f"⚠️ Auto-push failed: {push_msg}")

        logger.info(f"Response sent ({len(response)} chars)")

    except subprocess.TimeoutExpired:
        await status_msg.edit_text("Request timed out (5 min limit).")
    except Exception as e:
        await status_msg.edit_text(f"Error: {str(e)}")
        logger.error(f"Error: {e}")

# =============================================================================
# MAIN
# =============================================================================

def main():
    load_sessions()

    app = Application.builder().token(BOT_TOKEN).build()
    
    # Original commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", new_session))
    app.add_handler(CommandHandler("cwd", cwd_command))
    app.add_handler(CommandHandler("stream", stream_toggle))
    app.add_handler(CommandHandler("auto", auto_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("session", session_command))
    app.add_handler(CommandHandler("git", git_command))
    
    # NEW commands
    app.add_handler(CommandHandler("proj", proj_command))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("pull", pull_command))
    app.add_handler(CommandHandler("push", push_command))
    
    # Callback handlers for inline buttons
    app.add_handler(CallbackQueryHandler(handle_settings_callback, pattern="^(setauto_|setmodel_)"))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Starting Enhanced Droid Telegram bot...")
    logger.info(f"Allowed users: {ALLOWED_USERS}")
    logger.info(f"Project shortcuts: {list(PROJECT_SHORTCUTS.keys())}")
    logger.info(f"Auto git pull: {AUTO_GIT_PULL}, Auto git push: {AUTO_GIT_PUSH}")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
