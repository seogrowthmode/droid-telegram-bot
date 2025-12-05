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
import asyncio
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

# OpenAI API for session naming (optional - falls back to default naming if not set)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

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
AUTONOMY_LEVELS = ["off", "low", "medium", "high", "unsafe"]

# =============================================================================
# DEFAULT PROJECT SETTINGS (apply to /proj and /add unless overridden)
# =============================================================================
DEFAULT_AUTONOMY = os.environ.get("DROID_DEFAULT_AUTONOMY", "high")
DEFAULT_MODEL_SHORTCUT = os.environ.get("DROID_DEFAULT_MODEL", "opus")
DEFAULT_SYNC = os.environ.get("DROID_DEFAULT_SYNC", "true").lower() == "true"

# =============================================================================
# BOT FEATURES - Auto-synced to README.md via pre-commit hook
# Update this dict when adding features!
# =============================================================================
BOT_FEATURES = {
    "auto_git_sync": {
        "emoji": "🔄",
        "name": "Auto Git Sync",
        "desc": "Automatically pull before tasks and push after changes"
    },
    "project_shortcuts": {
        "emoji": "📁",
        "name": "Project Shortcuts",
        "desc": "Quick project switching with `/proj` command"
    },
    "smart_defaults": {
        "emoji": "⚡",
        "name": "Smart Defaults",
        "desc": "High autonomy, Opus model, sync on by default"
    },
    "task_queue": {
        "emoji": "📋",
        "name": "Task Queue",
        "desc": "Queue multiple tasks with `/add`, process with `/run`"
    },
    "smart_voice": {
        "emoji": "🎤",
        "name": "Smart Voice",
        "desc": "Voice commands with intent detection (add task, switch project, run queue)"
    },
    "auto_restart": {
        "emoji": "🔃",
        "name": "Auto-Restart",
        "desc": "Development mode with automatic reload on file changes"
    },
}

# =============================================================================
# SMART VOICE/MESSAGE ROUTING - Trigger phrases (modify these!)
# =============================================================================
VOICE_TRIGGERS = {
    "add_task": [
        "add a task", "add task", "queue up", "queue task",
        "add to queue", "put in queue", "schedule task", "schedule a task",
        "add to the queue", "put on queue", "queue a task"
    ],
    "switch_project": [
        "switch to", "go to", "open project", "work on project",
        "let's work on", "change to", "switch project to",
        "open up", "jump to", "move to"
    ],
    "show_queue": [
        "show queue", "what's in queue", "list queue", "view queue",
        "show tasks", "pending tasks", "what's queued", "show my queue",
        "what's in the queue", "list tasks", "my queue"
    ],
    "run_queue": [
        "run queue", "start queue", "process queue", "execute queue",
        "run tasks", "start tasks", "run the queue", "start the queue",
        "process tasks", "go through queue"
    ],
    "pause_queue": [
        "pause queue", "stop queue", "pause tasks", "hold queue"
    ],
    "clear_queue": [
        "clear queue", "empty queue", "delete tasks", "clear tasks",
        "remove all tasks", "clear the queue", "empty the queue"
    ]
}

# =============================================================================
# LLM SESSION NAMING
# =============================================================================
def generate_session_name(first_message: str, cwd: str = None) -> str:
    """
    Use LLM to generate a short, descriptive session name based on the first message.
    Falls back to random ID if OpenAI isn't configured or fails.
    """
    if not OPENAI_API_KEY or not first_message:
        return f"tg-{uuid.uuid4().hex[:8]}"
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Extract project name from cwd for context
        project_context = ""
        if cwd:
            project_name = os.path.basename(cwd.rstrip('/'))
            project_context = f" (project: {project_name})"
        
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """Generate a very short session name (2-4 words, max 25 chars) that describes the task.
Use PascalCase with no spaces. Be specific but concise.
Examples: "FixMobileNav", "AddDarkMode", "RefactorAuth", "UpdateDashboard", "DebugAPIError"
Output ONLY the name, nothing else."""
                },
                {
                    "role": "user",
                    "content": f"Task{project_context}: {first_message[:200]}"
                }
            ],
            max_tokens=30,
            temperature=0.3
        )
        
        name = response.choices[0].message.content.strip()
        # Clean up the name - remove quotes, spaces, special chars
        name = re.sub(r'[^a-zA-Z0-9]', '', name)
        
        if name and len(name) >= 3:
            return name[:25]  # Cap at 25 chars
        
    except ImportError:
        logging.warning("OpenAI package not installed - using default session naming")
    except Exception as e:
        logging.warning(f"Failed to generate session name via LLM: {e}")
    
    # Fallback to random ID
    return f"tg-{uuid.uuid4().hex[:8]}"


async def generate_session_name_async(first_message: str, cwd: str = None) -> str:
    """Async wrapper for generate_session_name"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, generate_session_name, first_message, cwd)


def detect_voice_intent(text: str) -> tuple:
    """
    Detect intent from voice/text message.
    Returns: (intent, project, remaining_text) or (None, None, text)
    """
    text_lower = text.lower().strip()
    
    # Check each intent
    for intent, triggers in VOICE_TRIGGERS.items():
        for trigger in triggers:
            if trigger in text_lower:
                # Found a trigger - extract the rest
                remaining = text_lower.replace(trigger, "").strip()
                
                # Try to find project name
                project = None
                for proj_name in PROJECT_SHORTCUTS.keys():
                    if proj_name in remaining:
                        project = proj_name
                        # Remove project name from remaining
                        remaining = remaining.replace(proj_name, "").strip()
                        # Clean up common connector words
                        for word in ["on", "to", "for", "in", "the", "project"]:
                            remaining = remaining.replace(f" {word} ", " ").strip()
                            if remaining.startswith(f"{word} "):
                                remaining = remaining[len(word)+1:].strip()
                            if remaining.endswith(f" {word}"):
                                remaining = remaining[:-(len(word)+1)].strip()
                        break
                
                return (intent, project, remaining.strip())
    
    return (None, None, text)


def fuzzy_match_project(text: str) -> str:
    """
    Try to fuzzy match project names in text.
    Handles common Whisper mishearings like 'chatics' -> 'chadix'
    """
    if not PROJECT_SHORTCUTS:
        return text
    
    text_lower = text.lower()
    
    for project in PROJECT_SHORTCUTS.keys():
        # Already exact match
        if project in text_lower:
            continue
        
        # Check for similar sounding words (simple edit distance)
        words = text_lower.split()
        for i, word in enumerate(words):
            # Skip short words
            if len(word) < 4:
                continue
            
            # Check if word is similar to project name
            # Simple check: same first letter and similar length
            if word[0] == project[0] and abs(len(word) - len(project)) <= 2:
                # Count matching characters
                matches = sum(1 for a, b in zip(word, project) if a == b)
                if matches >= len(project) * 0.6:  # 60% match
                    # Replace the word with correct project name
                    words[i] = project
                    return " ".join(words)
    
    return text


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
        return MODEL_SHORTCUTS.get(DEFAULT_MODEL_SHORTCUT)
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

# =============================================================================
# NEW FEATURE: Task Queue
# =============================================================================
task_queue = []  # List of queued tasks
queue_running = False  # Is queue currently processing
queue_paused = False  # Is queue paused

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
    
    shortcuts_list = ", ".join(PROJECT_SHORTCUTS.keys()) if PROJECT_SHORTCUTS else "(none)"
    first_project = list(PROJECT_SHORTCUTS.keys())[0] if PROJECT_SHORTCUTS else "myapp"
    
    await update.message.reply_text(
        "🤖 <b>Droid Telegram Bot</b>\n\n"
        f"<b>⚡ Defaults:</b> {DEFAULT_AUTONOMY} | {DEFAULT_MODEL_SHORTCUT} | {'sync' if DEFAULT_SYNC else 'nosync'}\n\n"
        
        "<b>📁 Projects:</b>\n"
        f"<code>/proj {first_project}</code> - Start with defaults\n"
        f"<code>/proj {first_project} @taskname</code> - Named session\n"
        f"<code>/proj {first_project} nosync</code> - No auto-push\n\n"
        
        "<b>📋 Task Queue:</b>\n"
        f"<code>/add {first_project} Build feature X</code>\n"
        f"<code>/add {first_project} Fix the login bug</code>\n"
        "<code>/run</code> - Process all queued tasks\n\n"
        
        "<b>🎤 Voice:</b> Just send a voice message!\n\n"
        
        "<b>📂 Git:</b> /pull /push /sync\n"
        "<b>⚙️ Session:</b> /new /session /auto /stop\n"
        "<b>📊 Info:</b> /status /cwd /queue\n\n"
        
        f"<b>Projects:</b> {shortcuts_list}\n"
        "<b>Models:</b> opus, sonnet, haiku, gpt, codex, gemini\n\n"
        "Type /features for full details",
        parse_mode=ParseMode.HTML
    )


async def features_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all bot features in detail"""
    if not is_authorized(update.effective_user.id):
        return
    
    shortcuts_display = "\n".join([f"  • <code>{k}</code> → {v}" for k, v in PROJECT_SHORTCUTS.items()]) if PROJECT_SHORTCUTS else "  (none configured)"
    
    await update.message.reply_text(
        "🤖 <b>FULL FEATURE LIST</b>\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>⚡ DEFAULT SETTINGS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"• Autonomy: <b>{DEFAULT_AUTONOMY}</b>\n"
        f"• Model: <b>{DEFAULT_MODEL_SHORTCUT}</b>\n"
        f"• Auto-sync: <b>{'ON' if DEFAULT_SYNC else 'OFF'}</b>\n"
        "These apply to /proj and /add unless overridden\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📁 PROJECT SHORTCUTS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"{shortcuts_display}\n\n"
        "<code>/proj name</code> - Switch (uses defaults)\n"
        "<code>/proj name @session</code> - With session name\n"
        "<code>/proj name sonnet</code> - Override model\n"
        "<code>/proj name nosync</code> - Disable auto-push\n"
        "<code>/proj name low haiku nosync</code> - Multiple overrides\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📋 TASK QUEUE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Queue up multiple tasks, run them all!\n\n"
        "<code>/add project Task description</code> - Add task\n"
        "<code>/add project medium Fix bug</code> - With autonomy\n"
        "<code>/queue</code> - View all tasks\n"
        "<code>/run</code> - Start processing queue\n"
        "<code>/pause</code> - Pause processing\n"
        "<code>/skip</code> - Skip current task\n"
        "<code>/clear</code> - Clear all tasks\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🎤 SMART VOICE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Voice is transcribed and routed smartly!\n\n"
        "<b>Say:</b>\n"
        "• \"Add task on chadix to build X\" → queues task\n"
        "• \"Switch to chadix\" → switches project\n"
        "• \"What's in my queue\" → shows queue\n"
        "• \"Run the queue\" → starts processing\n"
        "• \"Clear queue\" → clears all tasks\n"
        "• \"Fix the login bug\" → sends to current session\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🔄 AUTO GIT SYNC</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "When sync is ON (default):\n"
        "• Auto <code>git pull</code> before each task\n"
        "• Auto <code>git commit + push</code> after each task\n\n"
        "<code>/sync</code> - Toggle sync on/off\n"
        "<code>/pull</code> - Manual pull\n"
        "<code>/push [msg]</code> - Commit and push\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🤖 MODELS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "• <code>opus</code> - Claude Opus (default)\n"
        "• <code>sonnet</code> - Claude Sonnet\n"
        "• <code>haiku</code> - Claude Haiku (fast)\n"
        "• <code>gpt</code> - GPT-4.1\n"
        "• <code>codex</code> - Codex-1\n"
        "• <code>gemini</code> - Gemini Pro\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>⚙️ OTHER COMMANDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<code>/new [path]</code> - New session in directory\n"
        "<code>/session</code> - List/switch sessions\n"
        "<code>/auto [level]</code> - Set autonomy level\n"
        "<code>/status</code> - Bot and session status\n"
        "<code>/stop</code> - Stop running task\n"
        "<code>/cwd</code> - Show current directory\n"
        "<code>/git [cmd]</code> - Run git commands\n",
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
            lines.append(f"\n<b>Defaults:</b> {DEFAULT_AUTONOMY}, {DEFAULT_MODEL_SHORTCUT}, {'sync' if DEFAULT_SYNC else 'nosync'}")
            lines.append(f"\n<b>Usage:</b> <code>/proj shortcut [@name]</code>")
            lines.append(f"<b>Override:</b> add autonomy/model/nosync to change")
            lines.append(f"\n<b>Examples:</b>")
            lines.append(f"<code>/proj chadix</code> (uses defaults)")
            lines.append(f"<code>/proj chadix @homepage</code> (with name)")
            lines.append(f"<code>/proj chadix sonnet nosync</code> (override)")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
        return
    
    # Parse arguments: /proj <shortcut> [autonomy] [model] [sync] [@name]
    # Uses defaults: high autonomy, opus model, sync on
    shortcut = args[0].lower()
    autonomy_level = DEFAULT_AUTONOMY
    model_shortcut = DEFAULT_MODEL_SHORTCUT
    auto_sync = DEFAULT_SYNC
    session_name = None
    
    for arg in args[1:]:
        arg_lower = arg.lower()
        if arg.startswith("@"):
            session_name = arg[1:]  # Remove @ prefix
        elif arg_lower in AUTONOMY_LEVELS:
            autonomy_level = arg_lower
        elif arg_lower in ["sync", "push", "autopush"]:
            auto_sync = True
        elif arg_lower in ["nosync", "nopush"]:
            auto_sync = False
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
    # Use custom name if provided, otherwise generate random ID
    temp_session_id = session_name if session_name else f"tg-{str(uuid.uuid4())[:8]}"
    short_cwd = resolved_cwd.replace(os.path.expanduser("~"), "~")
    git_state, git_info = get_git_status(resolved_cwd)
    
    # Resolve model
    model_id = resolve_model(model_shortcut) if model_shortcut else None
    model_display = model_shortcut or "default"
    
    # Set autonomy, model, and git sync for this session
    session_autonomy[temp_session_id] = autonomy_level
    if model_id:
        session_models[temp_session_id] = model_id
    if auto_sync:
        session_git_sync[temp_session_id] = {"pull": True, "push": True}
    
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
    if auto_sync:
        status_lines.append(f"📤 Git: auto-push ON")
    
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
    
    # Add to session history so it shows in /session
    add_to_session_history(temp_session_id, resolved_cwd, f"(project: {shortcut})")
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
# NEW FEATURE: Task Queue
# =============================================================================

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a task to the queue: /add <project> [high/medium] [model] [sync] <task description>"""
    if not is_authorized(update.effective_user.id):
        return
    
    text = update.message.text[4:].strip()  # Remove "/add"
    if not text:
        await update.message.reply_text(
            "<b>📋 Add Task to Queue</b>\n\n"
            "<b>Usage:</b> <code>/add project [settings] task</code>\n\n"
            "<b>Examples:</b>\n"
            "<code>/add chadix Build the homepage</code>\n"
            "<code>/add chadix high sonnet Fix login bug</code>\n"
            "<code>/add chadix high sync Create user dashboard</code>\n\n"
            "<b>Settings:</b> autonomy (high/medium/low), model (sonnet/opus), sync",
            parse_mode=ParseMode.HTML
        )
        return
    
    parts = text.split()
    if len(parts) < 2:
        await update.message.reply_text("❌ Need project and task. Example: /add chadix Build feature X")
        return
    
    project = parts[0].lower()
    if project not in PROJECT_SHORTCUTS:
        available = ", ".join(PROJECT_SHORTCUTS.keys())
        await update.message.reply_text(f"❌ Unknown project: {project}\n\nAvailable: {available}")
        return
    
    # Parse settings and task description (uses global defaults)
    autonomy = DEFAULT_AUTONOMY
    model = DEFAULT_MODEL_SHORTCUT
    sync = DEFAULT_SYNC
    task_words = []
    
    for part in parts[1:]:
        part_lower = part.lower()
        if part_lower in AUTONOMY_LEVELS:
            autonomy = part_lower
        elif part_lower in ["sync", "push"]:
            sync = True
        elif part_lower in ["nosync", "nopush"]:
            sync = False
        elif part_lower in MODEL_SHORTCUTS:
            model = part_lower
        else:
            task_words.append(part)
    
    task_description = " ".join(task_words)
    if not task_description:
        await update.message.reply_text("❌ Need a task description")
        return
    
    # Create task
    task = {
        "id": str(uuid.uuid4())[:8],
        "project": project,
        "task": task_description,
        "autonomy": autonomy,
        "model": model,
        "sync": sync,
        "status": "pending",
        "added": datetime.now().isoformat()
    }
    
    task_queue.append(task)
    position = len(task_queue)
    
    model_display = model or "default"
    sync_display = "📤" if sync else ""
    
    await update.message.reply_text(
        f"✅ Added to queue (#{position})\n\n"
        f"📁 {project}\n"
        f"📝 {task_description}\n"
        f"⚡ {autonomy} | 🤖 {model_display} {sync_display}\n\n"
        f"Use /queue to view, /run to start",
        parse_mode=ParseMode.HTML
    )


async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View the task queue"""
    if not is_authorized(update.effective_user.id):
        return
    
    if not task_queue:
        await update.message.reply_text(
            "📋 <b>Queue is empty</b>\n\n"
            "Add tasks with:\n"
            "<code>/add chadix Build feature X</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    lines = ["📋 <b>Task Queue</b>\n"]
    
    status_emoji = {"pending": "⏳", "running": "🔄", "completed": "✅", "failed": "❌"}
    
    for i, task in enumerate(task_queue, 1):
        emoji = status_emoji.get(task["status"], "⏳")
        sync_icon = "📤" if task.get("sync") else ""
        lines.append(
            f"{emoji} <b>#{i}</b> [{task['project']}] {task['task'][:40]}{'...' if len(task['task']) > 40 else ''} {sync_icon}"
        )
    
    queue_status = "🔄 Running" if queue_running else ("⏸ Paused" if queue_paused else "⏹ Stopped")
    lines.append(f"\n<b>Status:</b> {queue_status}")
    lines.append("\n<b>Commands:</b> /run, /pause, /clear, /skip")
    
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start processing the queue"""
    global queue_running, queue_paused
    
    if not is_authorized(update.effective_user.id):
        return
    
    if not task_queue:
        await update.message.reply_text("📋 Queue is empty. Add tasks with /add")
        return
    
    pending_tasks = [t for t in task_queue if t["status"] == "pending"]
    if not pending_tasks:
        await update.message.reply_text("✅ All tasks completed! Use /clear to reset.")
        return
    
    if queue_running:
        await update.message.reply_text("🔄 Queue is already running")
        return
    
    queue_running = True
    queue_paused = False
    
    await update.message.reply_text(f"▶️ Starting queue ({len(pending_tasks)} tasks)...")
    
    # Process queue
    await process_queue(update, context)


async def process_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process tasks in the queue"""
    global queue_running, queue_paused
    
    user_id = update.effective_user.id
    
    while queue_running and not queue_paused:
        # Find next pending task
        pending = None
        for task in task_queue:
            if task["status"] == "pending":
                pending = task
                break
        
        if not pending:
            queue_running = False
            await update.message.reply_text("✅ Queue completed!")
            return
        
        # Mark as running
        pending["status"] = "running"
        
        # Resolve project path
        project = pending["project"]
        path = PROJECT_SHORTCUTS.get(project)
        if not path:
            pending["status"] = "failed"
            continue
        
        resolved_cwd = os.path.expanduser(path)
        
        # Git pull if sync enabled
        if pending.get("sync") and is_git_repo(resolved_cwd):
            git_pull(resolved_cwd)
        
        # Create session
        session_id = f"q-{pending['id']}"
        autonomy = pending.get("autonomy", "high")
        model = resolve_model(pending.get("model"))
        
        session_autonomy[session_id] = autonomy
        if model:
            session_models[session_id] = model
        if pending.get("sync"):
            session_git_sync[session_id] = {"pull": True, "push": True}
        
        # Update active session
        active_session_per_user[user_id] = {
            "session_id": session_id,
            "cwd": resolved_cwd
        }
        
        # Send status
        short_cwd = resolved_cwd.replace(os.path.expanduser("~"), "~")
        status_msg = await update.message.reply_text(
            f"🔄 <b>Task #{task_queue.index(pending) + 1}</b>\n"
            f"📁 {short_cwd}\n"
            f"📝 {pending['task'][:50]}...\n\n"
            f"Working...",
            parse_mode=ParseMode.HTML
        )
        
        try:
            # Run the task
            response, new_session_id = await handle_message_streaming(
                pending["task"],
                session_id,
                status_msg,
                resolved_cwd,
                autonomy,
                user_id=user_id,
                model=model
            )
            
            response = response or "No response"
            if len(response) > 3000:
                response = response[:3000] + "\n\n[truncated]"
            
            await status_msg.delete()
            await send_formatted_message(update.message, f"✅ <b>Task completed:</b> {pending['task'][:30]}...\n\n{response}")
            
            # Git push if sync enabled
            if pending.get("sync") and is_git_repo(resolved_cwd) and git_has_changes(resolved_cwd):
                success, msg = git_commit_and_push(resolved_cwd, f"Task: {pending['task'][:50]}")
                if success:
                    await update.message.reply_text(f"📤 Pushed: {msg}")
            
            pending["status"] = "completed"
            
        except Exception as e:
            pending["status"] = "failed"
            await status_msg.edit_text(f"❌ Task failed: {str(e)}")
            logger.error(f"Queue task failed: {e}")
        
        # Small delay between tasks
        await asyncio.sleep(2)
    
    queue_running = False


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause the queue"""
    global queue_paused, queue_running
    
    if not is_authorized(update.effective_user.id):
        return
    
    if not queue_running:
        await update.message.reply_text("Queue is not running")
        return
    
    queue_paused = True
    await update.message.reply_text("⏸ Queue paused. Use /run to resume.")


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear the queue"""
    global task_queue, queue_running, queue_paused
    
    if not is_authorized(update.effective_user.id):
        return
    
    count = len(task_queue)
    task_queue = []
    queue_running = False
    queue_paused = False
    
    await update.message.reply_text(f"🗑 Cleared {count} tasks from queue")


async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip the current task"""
    if not is_authorized(update.effective_user.id):
        return
    
    for task in task_queue:
        if task["status"] == "running":
            task["status"] = "failed"
            await update.message.reply_text(f"⏭ Skipped: {task['task'][:30]}...")
            return
    
    await update.message.reply_text("No task currently running")


# =============================================================================
# NEW FEATURE: Voice Message Support with Smart Routing
# =============================================================================

async def route_voice_intent(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                             intent: str, project: str, task_text: str, status_msg) -> bool:
    """
    Route detected intent to appropriate command.
    Returns True if handled, False to continue with normal processing.
    """
    global queue_running, queue_paused
    user_id = update.effective_user.id
    
    if intent == "add_task":
        if not project:
            await status_msg.edit_text(
                f"🎤 Detected: Add task\n\n"
                f"❌ No project specified. Say: \"Add task ON CHADIX to build X\"\n\n"
                f"Available projects: {', '.join(PROJECT_SHORTCUTS.keys())}"
            )
            return True
        if not task_text:
            await status_msg.edit_text(f"🎤 Detected: Add task on {project}\n\n❌ No task description provided")
            return True
        
        # Add to queue
        task = {
            "id": str(uuid.uuid4())[:8],
            "project": project,
            "task": task_text,
            "autonomy": DEFAULT_AUTONOMY,
            "model": DEFAULT_MODEL_SHORTCUT,
            "sync": DEFAULT_SYNC,
            "status": "pending",
            "added": datetime.now().isoformat()
        }
        task_queue.append(task)
        
        await status_msg.edit_text(
            f"🎤 ✅ Added to queue!\n\n"
            f"📁 Project: {project}\n"
            f"📝 Task: {task_text}\n"
            f"⚡ {DEFAULT_AUTONOMY} | 🤖 {DEFAULT_MODEL_SHORTCUT}\n\n"
            f"Queue now has {len(task_queue)} task(s). /run to start"
        )
        return True
    
    elif intent == "switch_project":
        if not project:
            await status_msg.edit_text(
                f"🎤 Detected: Switch project\n\n"
                f"❌ No project specified. Say: \"Switch to CHADIX\"\n\n"
                f"Available: {', '.join(PROJECT_SHORTCUTS.keys())}"
            )
            return True
        
        # Switch project using defaults
        path = PROJECT_SHORTCUTS.get(project)
        resolved_cwd = os.path.expanduser(path)
        session_id = f"tg-{uuid.uuid4().hex[:8]}"
        
        session_autonomy[session_id] = DEFAULT_AUTONOMY
        session_models[session_id] = resolve_model(DEFAULT_MODEL_SHORTCUT)
        if DEFAULT_SYNC:
            session_git_sync[session_id] = {"pull": True, "push": True}
        
        active_session_per_user[user_id] = {
            "session_id": session_id,
            "cwd": resolved_cwd
        }
        
        # Add to session history
        add_to_session_history(session_id, resolved_cwd, f"(voice: {project})")
        save_sessions()
        
        short_cwd = resolved_cwd.replace(os.path.expanduser("~"), "~")
        await status_msg.edit_text(
            f"🎤 ✅ Switched to {project}!\n\n"
            f"📁 {short_cwd}\n"
            f"⚡ {DEFAULT_AUTONOMY} | 🤖 {DEFAULT_MODEL_SHORTCUT} | {'📤 sync' if DEFAULT_SYNC else ''}\n\n"
            f"Ready for commands!"
        )
        return True
    
    elif intent == "show_queue":
        if not task_queue:
            await status_msg.edit_text("🎤 📋 Queue is empty\n\nAdd tasks with voice: \"Add task on chadix to build X\"")
        else:
            lines = [f"🎤 📋 Queue ({len(task_queue)} tasks)\n"]
            status_emoji = {"pending": "⏳", "running": "🔄", "completed": "✅", "failed": "❌"}
            for i, task in enumerate(task_queue[:5], 1):  # Show first 5
                emoji = status_emoji.get(task["status"], "⏳")
                lines.append(f"{emoji} {task['project']}: {task['task'][:30]}...")
            if len(task_queue) > 5:
                lines.append(f"\n...and {len(task_queue) - 5} more")
            lines.append("\n\nSay \"run queue\" to start!")
            await status_msg.edit_text("\n".join(lines))
        return True
    
    elif intent == "run_queue":
        if not task_queue:
            await status_msg.edit_text("🎤 Queue is empty - nothing to run")
            return True
        pending = [t for t in task_queue if t["status"] == "pending"]
        if not pending:
            await status_msg.edit_text("🎤 All tasks completed! Say \"clear queue\" to reset")
            return True
        
        queue_running = True
        queue_paused = False
        await status_msg.edit_text(f"🎤 ▶️ Starting queue ({len(pending)} tasks)...")
        await process_queue(update, context)
        return True
    
    elif intent == "pause_queue":
        queue_paused = True
        await status_msg.edit_text("🎤 ⏸ Queue paused")
        return True
    
    elif intent == "clear_queue":
        count = len(task_queue)
        task_queue.clear()
        queue_running = False
        queue_paused = False
        await status_msg.edit_text(f"🎤 🗑 Cleared {count} tasks from queue")
        return True
    
    return False


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
        
        # Build initial_prompt with project names and trigger words for better accuracy
        project_names = ", ".join(PROJECT_SHORTCUTS.keys()) if PROJECT_SHORTCUTS else ""
        initial_prompt = f"Project names: {project_names}. Commands: add task, queue, switch to, run queue, clear queue, show queue."
        
        try:
            import whisper
            model = whisper.load_model("medium")  # medium for better accuracy
            result = model.transcribe(tmp_path, initial_prompt=initial_prompt)
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
                        [whisper_cmd, tmp_path, "--model", "medium", "--output_format", "txt", "--output_dir", "/tmp"],
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
        
        # Apply fuzzy matching to fix common mishearings (e.g., 'chatics' -> 'chadix')
        transcribed_text = fuzzy_match_project(transcribed_text)
        
        await status_msg.edit_text(f"🎤 \"{transcribed_text}\"\n\nProcessing...")
        
        user_id = update.effective_user.id
        
        # Smart routing - detect intent from voice
        intent, project, task_text = detect_voice_intent(transcribed_text)
        
        if intent:
            await status_msg.edit_text(f"🎤 \"{transcribed_text}\"\n\n🧠 Detected: {intent}")
            routed = await route_voice_intent(update, context, intent, project, task_text, status_msg)
            if routed:
                return
        
        # No intent detected - send to current session as normal
        session_id = None
        session_cwd = DEFAULT_CWD
        if user_id in active_session_per_user:
            active = active_session_per_user[user_id]
            session_id = active.get("session_id")
            session_cwd = active.get("cwd") or DEFAULT_CWD
        
        autonomy_level = session_autonomy.get(session_id, "off") if session_id else "off"
        model = session_models.get(session_id) if session_id else None
        
        # Call Droid
        if streaming_mode:
            response, new_session_id = await handle_message_streaming(
                transcribed_text, session_id, status_msg, session_cwd, autonomy_level, user_id=user_id, model=model
            )
        else:
            response, new_session_id = await handle_message_simple(
                transcribed_text, session_id, session_cwd, autonomy_level, model=model
            )
        
        response = response or "No response from Droid"
        if len(response) > 4000:
            response = response[:4000] + "\n\n[Response truncated]"
        
        await status_msg.delete()
        reply_msg = await send_formatted_message(update.message, response)
        
        # Update session tracking
        actual_session_id = new_session_id or session_id
        if actual_session_id:
            sessions[reply_msg.message_id] = {
                "session_id": actual_session_id,
                "cwd": session_cwd
            }
            active_session_per_user[user_id] = {
                "session_id": actual_session_id,
                "cwd": session_cwd,
                "last_msg_id": reply_msg.message_id
            }
            save_sessions()
        
    except Exception as e:
        logger.error(f"Voice message error: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)}")

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
        # Build list of sessions, ensuring active session is included
        all_sessions = list(session_history)
        
        # Add active session if not in history
        if user_id in active_session_per_user:
            active = active_session_per_user[user_id]
            active_sid = active.get("session_id")
            if active_sid and not any(s.get("session_id") == active_sid for s in all_sessions):
                all_sessions.append({
                    "session_id": active_sid,
                    "cwd": active.get("cwd", ""),
                    "first_message": "(active session)"
                })
        
        if not all_sessions:
            await update.message.reply_text("No sessions yet. Use /new or /proj to start one.")
            return

        lines = ["<b>Recent Sessions</b>\n"]
        for entry in reversed(all_sessions[-10:]):
            sid = entry["session_id"][:8] if entry.get("session_id") else "unknown"
            cwd = entry.get("cwd", "").replace(os.path.expanduser("~"), "~")
            msg = entry.get("first_message", "")[:30] or "N/A"

            current = ""
            if user_id in active_session_per_user:
                if active_session_per_user[user_id].get("session_id") == entry.get("session_id"):
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
        
        # Try to find session_data from sessions dict using last_msg_id
        last_msg_id = active.get("last_msg_id")
        if last_msg_id and last_msg_id in sessions:
            session_data = sessions[last_msg_id]
    
    # Check if this is the first message for an awaiting session - generate LLM name
    is_first_message = False
    if session_data and isinstance(session_data, dict) and session_data.get("awaiting_first_message"):
        is_first_message = True
        old_session_id = session_id
        
        # Generate LLM-based session name
        new_name = await generate_session_name_async(user_message, session_cwd)
        logger.info(f"Generated LLM session name: {new_name} (was: {old_session_id})")
        
        # Migrate session data from old ID to new name
        if old_session_id and old_session_id != new_name:
            # Copy settings to new session ID
            if old_session_id in session_autonomy:
                session_autonomy[new_name] = session_autonomy.pop(old_session_id)
            if old_session_id in session_models:
                session_models[new_name] = session_models.pop(old_session_id)
            if old_session_id in session_git_sync:
                session_git_sync[new_name] = session_git_sync.pop(old_session_id)
        
        session_id = new_name
        session_data["session_id"] = new_name
        session_data["awaiting_first_message"] = False
        
        # Update active session
        if user_id in active_session_per_user:
            active_session_per_user[user_id]["session_id"] = new_name

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
    if is_first_message and session_id:
        status_text = f"🆔 {session_id}\n{status_text}"
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
    app.add_handler(CommandHandler("features", features_command))
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
    
    # Queue commands
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("queue", queue_command))
    app.add_handler(CommandHandler("run", run_command))
    app.add_handler(CommandHandler("pause", pause_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("skip", skip_command))
    
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
