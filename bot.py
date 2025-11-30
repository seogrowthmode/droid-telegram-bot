#!/usr/bin/env python3
"""
Droid Telegram Bot - A Telegram interface for Factory's Droid CLI

This bot allows you to interact with Droid via Telegram messages,
with live streaming of tool calls and session management.

Repository: https://github.com/anthropics/droid-telegram
License: MIT
"""
import subprocess
import logging
import os
import json
import select
import uuid
import re
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
from datetime import datetime

# Configuration from environment variables
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_IDS = os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "")  # Comma-separated list
LOG_FILE = os.environ.get("DROID_LOG_FILE", "/var/log/droid-telegram/bot.log")
SESSIONS_FILE = os.environ.get("DROID_SESSIONS_FILE", "/var/lib/droid-telegram/sessions.json")
DROID_PATH = os.environ.get("DROID_PATH", "droid")  # Path to droid CLI
DEFAULT_CWD = os.environ.get("DROID_DEFAULT_CWD", os.path.expanduser("~"))

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
    """Check if a user is authorized to use the bot"""
    if not ALLOWED_USERS:
        # If no users specified, deny all (secure by default)
        return False
    return user_id in ALLOWED_USERS

# Ensure directories exist
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
os.makedirs(os.path.dirname(SESSIONS_FILE), exist_ok=True)

# State
streaming_mode = True  # Default on
sessions = {}  # message_id -> {session_id, cwd, header_msg_id}
session_headers = {}  # Store header message ids for active sessions
active_session_per_user = {}  # user_id -> {session_id, cwd, last_msg_id} - fallback for non-reply messages
pending_permissions = {}  # request_id -> {user_message, session_id, cwd, user_id, chat_id, original_msg_id}
session_history = []  # List of all sessions with metadata for /session command

def load_sessions():
    """Load sessions from JSON file"""
    global sessions, active_session_per_user, session_history
    try:
        if os.path.exists(SESSIONS_FILE):
            with open(SESSIONS_FILE, 'r') as f:
                data = json.load(f)
                sessions = {int(k): v for k, v in data.get("sessions", {}).items()}
                active_session_per_user = {int(k): v for k, v in data.get("active_session_per_user", {}).items()}
                session_history = data.get("session_history", [])
                logger.info(f"Loaded {len(sessions)} sessions, {len(session_history)} history entries")
    except Exception as e:
        logger.error(f"Failed to load sessions: {e}")

def save_sessions():
    """Save sessions to JSON file"""
    try:
        data = {
            "sessions": {str(k): v for k, v in sessions.items()},
            "active_session_per_user": {str(k): v for k, v in active_session_per_user.items()},
            "session_history": session_history[-100:]  # Keep last 100 sessions
        }
        with open(SESSIONS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save sessions: {e}")

def add_to_session_history(session_id, cwd, first_message=None):
    """Add a session to history"""
    if not session_id:
        return
    # Check if already exists
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

def markdown_to_html(text):
    """Convert markdown to Telegram HTML format"""
    if not text:
        return text
    
    # Extract code blocks first to protect them
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"§§CODEBLOCK{len(code_blocks)-1}§§"
    
    # Save fenced code blocks ```...```
    text = re.sub(r'```(\w*)\n(.*?)```', save_code_block, text, flags=re.DOTALL)
    
    # Save inline code `...`
    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(1))
        return f"§§INLINECODE{len(inline_codes)-1}§§"
    text = re.sub(r'(?<!`)`([^`]+)`(?!`)', save_inline_code, text)
    
    # Escape HTML in the remaining text
    text = html.escape(text)
    
    # Convert markdown formatting to HTML
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', r'<i>\1</i>', text)
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'^[\-\*]\s+', '• ', text, flags=re.MULTILINE)
    
    # Restore inline code
    for i, code in enumerate(inline_codes):
        escaped_code = html.escape(code)
        text = text.replace(f"§§INLINECODE{i}§§", f"<code>{escaped_code}</code>")
    
    # Restore code blocks
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
    """Send a message with HTML formatting, falling back to plain text if needed"""
    try:
        html_text = markdown_to_html(text)
        return await message.reply_text(html_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"HTML parsing failed, sending plain text: {e}")
        return await message.reply_text(text)

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized. Contact the bot administrator.")
        return
    stream_status = "ON" if streaming_mode else "OFF"
    await update.message.reply_text(
        "🤖 Droid Telegram Bot ready!\n\n"
        "Commands:\n"
        "/new <path> - Start new session in directory\n"
        "/cwd - Show/change working directory\n"
        f"/stream - Toggle live updates (currently: {stream_status})\n"
        "/session - List/switch sessions\n"
        "/status - Check bot status\n"
        "/help - Show detailed help\n\n"
        "💡 Reply to any message to continue that session"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    stream_status = "ON" if streaming_mode else "OFF"
    await update.message.reply_text(
        "🤖 <b>Droid Telegram Bot Help</b>\n\n"
        "This bot connects to Factory's Droid CLI.\n\n"
        "<b>📝 Usage:</b>\n"
        "• Send any message to interact with Droid\n"
        "• Reply to a message to continue that session\n"
        "• Use /new to start fresh in a directory\n\n"
        "<b>⚙️ Commands:</b>\n"
        "/start - Welcome message\n"
        "/help - This help\n"
        "/new [path] - New session (optional directory)\n"
        "/session - List/switch sessions\n"
        "/cwd - Show current working directory\n"
        f"/stream - Toggle live tool updates ({stream_status})\n"
        "/status - Bot and Droid status\n"
        "/git [cmd] - Quick git commands\n\n"
        "<b>💡 Tips:</b>\n"
        "• Live updates show which tools Droid uses\n"
        "• Sessions persist across messages\n"
        "• Timeout is 5 minutes per request",
        parse_mode=ParseMode.HTML
    )

def resolve_cwd(path_arg):
    """Resolve a path argument to an absolute path for cwd"""
    if not path_arg:
        return DEFAULT_CWD
    
    # If it starts with /, it's already absolute
    if path_arg.startswith("/"):
        resolved = path_arg
    # If it starts with ~, expand it
    elif path_arg.startswith("~"):
        resolved = os.path.expanduser(path_arg)
    # Otherwise assume it's relative to DEFAULT_CWD
    else:
        resolved = os.path.join(DEFAULT_CWD, path_arg)
    
    # Verify the path exists
    if os.path.isdir(resolved):
        return resolved
    else:
        return None

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

async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a new session - supports /new <path> to set working directory"""
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
    
    # Send header with git status
    short_cwd = resolved_cwd.replace(os.path.expanduser("~"), "~")
    git_state, git_info = get_git_status(resolved_cwd)
    if git_state == "clean":
        git_line = f"✓ Git: {git_info}"
    elif git_state == "dirty":
        git_line = f"⚠️ Git: {git_info}"
    else:
        git_line = f"Git: {git_info}"
    
    header_text = f"📂 {short_cwd}\n🆔 Session: {temp_session_ref}\n{git_line}"
    header_msg = await update.message.reply_text(header_text)
    
    if prompt:
        status_text = "Working..." if streaming_mode else "Thinking..."
        status_msg = await update.message.reply_text(status_text)
        
        try:
            if streaming_mode:
                response, session_id = await handle_message_streaming(prompt, None, status_msg, resolved_cwd)
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
            
            if session_id:
                short_session = session_id[:8] if len(session_id) > 8 else session_id
                new_header = f"📂 {short_cwd}\n🆔 Session: {short_session}"
                try:
                    await header_msg.edit_text(new_header)
                except:
                    pass
            
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
            f"🆕 New session started in `{short_cwd}`\n\nReply to continue.",
            parse_mode="Markdown"
        )
        sessions[instruction_msg.message_id] = session_data
        
        active_session_per_user[user_id] = {
            "session_id": None,
            "cwd": resolved_cwd,
            "last_msg_id": instruction_msg.message_id
        }
        save_sessions()

async def cwd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show or change current working directory"""
    if not is_authorized(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    if user_id in active_session_per_user:
        cwd = active_session_per_user[user_id].get("cwd") or DEFAULT_CWD
    else:
        cwd = DEFAULT_CWD
    
    short_cwd = cwd.replace(os.path.expanduser("~"), "~")
    git_state, git_info = get_git_status(cwd)
    
    await update.message.reply_text(
        f"📂 Current directory: `{short_cwd}`\n"
        f"Git: {git_info}\n\n"
        f"Use /new <path> to change directory",
        parse_mode="Markdown"
    )

async def stream_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global streaming_mode
    if not is_authorized(update.effective_user.id):
        return
    streaming_mode = not streaming_mode
    status = "ON" if streaming_mode else "OFF"
    await update.message.reply_text(f"Live tool updates: {status}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    try:
        droid_result = subprocess.run([DROID_PATH, "--version"], capture_output=True, text=True, timeout=10)
        droid_version = droid_result.stdout.strip() or "unknown"
        stream_status = "ON" if streaming_mode else "OFF"
        active_sessions = len(sessions)
        
        user_id = update.effective_user.id
        active_user_session = ""
        if user_id in active_session_per_user:
            active = active_session_per_user[user_id]
            sid = active.get("session_id", "")[:8] if active.get("session_id") else "pending"
            cwd = active.get("cwd", "").replace(os.path.expanduser("~"), "~") if active.get("cwd") else "default"
            active_user_session = f"\n\nYour active session: {sid} in {cwd}"
        
        status_msg = (f"✅ Bot Status: Running\n"
                     f"🤖 Droid: {droid_version}\n"
                     f"⚡ Live updates: {stream_status}\n"
                     f"📊 Active sessions: {active_sessions}{active_user_session}")
        await update.message.reply_text(status_msg)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

async def git_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute git commands"""
    if not is_authorized(update.effective_user.id):
        return
    
    user_id = update.effective_user.id
    
    if user_id in active_session_per_user:
        cwd = active_session_per_user[user_id].get("cwd") or DEFAULT_CWD
    else:
        cwd = DEFAULT_CWD
    
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
        
        await status_msg.edit_text(f"📂 {short_cwd}\n```\n$ git {git_args}\n{output}\n```", parse_mode="Markdown")
    except subprocess.TimeoutExpired:
        await status_msg.edit_text("Command timed out")
    except Exception as e:
        await status_msg.edit_text(f"Error: {e}")

async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List sessions or switch to a specific session"""
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
                f"✓ Switched to session `{found['session_id'][:8]}`\n"
                f"📂 {short_cwd}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(f"Session not found: {target}")
    else:
        if not session_history:
            await update.message.reply_text("No sessions yet. Use /new to start one.")
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

async def handle_permission_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Allow/Deny button presses for permission requests"""
    query = update.callback_query
    await query.answer()
    
    if not is_authorized(query.from_user.id):
        return
    
    data = query.data
    if not data.startswith("perm_"):
        return
    
    parts = data.split("_")
    action = parts[1]
    request_id = parts[2]
    
    if request_id not in pending_permissions:
        await query.edit_message_text("Permission request expired.")
        return
    
    req = pending_permissions.pop(request_id)
    
    if action == "deny":
        await query.edit_message_text("❌ Action denied.")
        return
    
    await query.edit_message_text("✓ Allowed. Re-running with permissions...")
    
    status_msg = await context.bot.send_message(
        chat_id=req["chat_id"],
        text="Working... (with elevated permissions)"
    )
    
    try:
        response, new_session_id = await handle_message_streaming_unsafe(
            req["user_message"], 
            req["session_id"], 
            status_msg, 
            req["cwd"]
        )
        
        response = response or "No response from Droid"
        if len(response) > 4000:
            response = response[:4000] + "\n\n[Response truncated]"
        
        await status_msg.delete()
        reply_msg = await context.bot.send_message(
            chat_id=req["chat_id"],
            text=markdown_to_html(response),
            parse_mode=ParseMode.HTML
        )
        
        actual_session_id = new_session_id or req["session_id"]
        sessions[reply_msg.message_id] = {
            "session_id": actual_session_id,
            "cwd": req["cwd"],
            "header_msg_id": None
        }
        active_session_per_user[req["user_id"]] = {
            "session_id": actual_session_id,
            "cwd": req["cwd"],
            "last_msg_id": reply_msg.message_id
        }
        save_sessions()
        
    except Exception as e:
        await status_msg.edit_text(f"Error: {str(e)}")

async def handle_message_streaming_unsafe(user_message, session_id, status_msg, cwd=None):
    """Handle message with --skip-permissions-unsafe flag"""
    
    env = os.environ.copy()
    working_dir = cwd or DEFAULT_CWD
    
    cmd = [DROID_PATH, "exec", "--auto", "high", "--skip-permissions-unsafe", "--output-format", "stream-json"]
    if session_id:
        cmd.extend(["-s", session_id])
    cmd.append(user_message)
    
    logger.info(f"Running droid UNSAFE in cwd: {working_dir}")
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=working_dir,
        bufsize=1
    )
    
    final_response = ""
    new_session_id = None
    tool_updates = []
    last_update = ""
    
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
                new_status = "Working... (elevated)\n\n" + "\n".join(display_tools)
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
    return final_response.strip(), new_session_id

def format_tool_call(data):
    """Format a tool call with relevant details for display"""
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
            if len(file_path) > 50:
                detail = "..." + file_path[-47:]
            else:
                detail = file_path
    elif tool_name == "Grep":
        pattern = params.get("pattern", "")
        if pattern:
            short_pattern = pattern[:20] + "..." if len(pattern) > 20 else pattern
            detail = f"'{short_pattern}'"
    elif tool_name == "Glob":
        patterns = params.get("patterns", [])
        if patterns and isinstance(patterns, list):
            detail = ", ".join(patterns[:2])
    elif tool_name == "LS":
        dir_path = params.get("directory_path", "") or params.get("path", "")
        if dir_path:
            detail = dir_path.split("/")[-1] if "/" in dir_path else dir_path
    elif tool_name == "Execute":
        cmd = params.get("command", "")
        if cmd:
            detail = cmd[:40] + "..." if len(cmd) > 40 else cmd
    elif tool_name == "WebSearch":
        query = params.get("query", "")
        if query:
            detail = f"'{query[:25]}...'" if len(query) > 25 else f"'{query}'"
    
    if detail:
        return f"→ {tool_name}: {detail}"
    else:
        return f"→ {tool_name}"

def extract_final_text(line):
    """Extract finalText from a completion JSON line"""
    if '"finalText"' not in line:
        return None
    try:
        data = json.loads(line)
        return data.get("finalText", "")
    except json.JSONDecodeError:
        try:
            start = line.find('"finalText":"') + len('"finalText":"')
            if start > len('"finalText":"'):
                rest = line[start:]
                end_patterns = ['","numTurns"', '","durationMs"', '","session_id"']
                end_pos = len(rest)
                for pattern in end_patterns:
                    pos = rest.find(pattern)
                    if pos != -1 and pos < end_pos:
                        end_pos = pos
                text = rest[:end_pos]
                return text.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
        except:
            pass
    return None

def extract_session_id(line):
    """Extract session_id from a completion JSON line"""
    try:
        data = json.loads(line)
        return data.get("session_id")
    except json.JSONDecodeError:
        try:
            if '"session_id":"' in line:
                start = line.find('"session_id":"') + len('"session_id":"')
                rest = line[start:]
                end = rest.find('"')
                if end != -1:
                    return rest[:end]
        except:
            pass
    return None

async def handle_message_streaming(user_message, session_id, status_msg, cwd=None):
    """Handle message with streaming tool updates. Returns (response, session_id)"""
    
    env = os.environ.copy()
    working_dir = cwd or DEFAULT_CWD
    
    cmd = [DROID_PATH, "exec", "--auto", "high", "--output-format", "stream-json"]
    if session_id:
        cmd.extend(["-s", session_id])
        logger.info(f"Continuing session: {session_id}")
    cmd.append(user_message)
    
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
    
    last_update = ""
    final_response = ""
    new_session_id = None
    tool_updates = []
    all_output = []
    
    while True:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                break
            continue
            
        line = line.strip()
        if not line:
            continue
        
        all_output.append(line)
        logger.info(f"Stream: {line[:150]}...")
        
        try:
            data = json.loads(line)
            event_type = data.get("type", "")
            
            if event_type == "tool_call":
                tool_display = format_tool_call(data)
                tool_updates.append(tool_display)
                display_tools = tool_updates[-5:]
                session_indicator = " (continuing)" if session_id else ""
                new_status = f"Working...{session_indicator}\n\n" + "\n".join(display_tools)
                if new_status != last_update:
                    try:
                        await status_msg.edit_text(new_status)
                        last_update = new_status
                    except:
                        pass
            
            elif event_type == "completion":
                final_response = data.get("finalText", "")
                new_session_id = data.get("session_id")
                        
            elif event_type == "text":
                text = data.get("text", "")
                if text:
                    final_response += text
                    
            elif event_type == "error":
                error_msg = data.get("message", "Unknown error")
                final_response = f"Error: {error_msg}"
                
        except json.JSONDecodeError as e:
            extracted = extract_final_text(line)
            if extracted:
                final_response = extracted
            if not new_session_id:
                new_session_id = extract_session_id(line)
    
    remaining_out = process.stdout.read()
    if remaining_out:
        remaining_out = remaining_out.strip()
        all_output.append(remaining_out)
        
        if not final_response:
            for line in remaining_out.split('\n'):
                line = line.strip()
                if line:
                    extracted = extract_final_text(line)
                    if extracted:
                        final_response = extracted
                        break
        
        if not new_session_id:
            for line in remaining_out.split('\n'):
                line = line.strip()
                if line:
                    new_session_id = extract_session_id(line)
                    if new_session_id:
                        break
    
    stderr = process.stderr.read()
    if stderr:
        logger.warning(f"Stderr: {stderr[:500]}")
        if not final_response:
            final_response = stderr.strip()
    
    process.wait()
    
    if not final_response and all_output:
        for line in reversed(all_output):
            extracted = extract_final_text(line)
            if extracted:
                final_response = extracted
                break
    
    return final_response.strip(), new_session_id

async def handle_message_simple(user_message, session_id, cwd=None):
    """Handle message without streaming. Returns (response, session_id)"""
    
    env = os.environ.copy()
    working_dir = cwd or DEFAULT_CWD
    
    cmd = [DROID_PATH, "exec", "--auto", "high"]
    if session_id:
        cmd.extend(["-s", session_id])
    cmd.append(user_message)
    
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
        logger.warning(f"Unauthorized access attempt from user {user_id}")
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
        else:
            if user_id in active_session_per_user:
                active = active_session_per_user[user_id]
                session_id = active.get("session_id")
                session_cwd = active.get("cwd") or DEFAULT_CWD
                is_continuation = True
    else:
        if user_id in active_session_per_user:
            active = active_session_per_user[user_id]
            session_id = active.get("session_id")
            session_cwd = active.get("cwd") or DEFAULT_CWD
            is_continuation = True
    
    short_cwd = session_cwd.replace(os.path.expanduser("~"), "~")
    short_session = session_id[:8] if session_id else "new"
    status_text = f"Working in {short_cwd}" if streaming_mode else f"Thinking in {short_cwd}"
    if is_continuation and session_id:
        status_text += f" (session {short_session})"
    status_msg = await update.message.reply_text(status_text)
    
    try:
        if streaming_mode:
            response, new_session_id = await handle_message_streaming(user_message, session_id, status_msg, session_cwd)
        else:
            response, new_session_id = await handle_message_simple(user_message, session_id, session_cwd)
        
        response = response or "No response from Droid"
        
        # Check for permission errors
        if "insufficient permission" in response.lower() or "skip-permissions-unsafe" in response.lower():
            await status_msg.delete()
            
            request_id = str(uuid.uuid4())[:8]
            pending_permissions[request_id] = {
                "user_message": user_message,
                "session_id": new_session_id or session_id,
                "cwd": session_cwd,
                "user_id": user_id,
                "chat_id": update.message.chat_id,
                "original_msg_id": update.message.message_id
            }
            
            keyboard = [
                [
                    InlineKeyboardButton("✓ Allow", callback_data=f"perm_allow_{request_id}"),
                    InlineKeyboardButton("✗ Deny", callback_data=f"perm_deny_{request_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "⚠️ Droid needs elevated permissions to proceed.\n\n"
                "This action requires `--skip-permissions-unsafe` flag.",
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return
        
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
        logger.info(f"Response sent ({len(response)} chars)")
        
    except subprocess.TimeoutExpired:
        await status_msg.edit_text("Request timed out (5 min limit).")
    except Exception as e:
        await status_msg.edit_text(f"Error: {str(e)}")
        logger.error(f"Error: {e}")

def main():
    load_sessions()
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", new_session))
    app.add_handler(CommandHandler("cwd", cwd_command))
    app.add_handler(CommandHandler("stream", stream_toggle))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("session", session_command))
    app.add_handler(CommandHandler("git", git_command))
    app.add_handler(CallbackQueryHandler(handle_permission_callback, pattern="^perm_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Starting Droid Telegram bot...")
    logger.info(f"Allowed users: {ALLOWED_USERS}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
