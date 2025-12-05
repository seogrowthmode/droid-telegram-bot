#!/usr/bin/env python3
"""
Auto-update README.md with commands extracted from bot.py
Run this before committing to keep docs in sync.
"""
import re
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
BOT_FILE = os.path.join(PROJECT_DIR, "bot.py")
README_FILE = os.path.join(PROJECT_DIR, "README.md")

# Command descriptions (maintain this when adding new commands)
COMMAND_DOCS = {
    # Core commands
    "start": ("Welcome message and quick help", "core"),
    "help": ("Quick help and examples", "core"),
    "features": ("Full feature list with all details", "core"),
    "new": ("Start new session (optionally in directory)", "core"),
    "session": ("List/switch sessions", "core"),
    "auto": ("Set autonomy level (off/low/medium/high/unsafe)", "core"),
    "cwd": ("Show current working directory", "core"),
    "stream": ("Toggle live tool updates on/off", "core"),
    "status": ("Bot and Droid status", "core"),
    "stop": ("Stop currently running task", "core"),
    "git": ("Run git commands in current directory", "core"),
    # Enhanced commands
    "proj": ("Switch project (defaults: high, opus, sync)", "enhanced"),
    "sync": ("Toggle auto git sync options", "enhanced"),
    "pull": ("Manually pull latest changes", "enhanced"),
    "push": ("Commit all changes and push", "enhanced"),
    # Queue commands
    "add": ("Add task to queue", "enhanced"),
    "queue": ("View task queue", "enhanced"),
    "run": ("Start processing queue", "enhanced"),
    "pause": ("Pause queue processing", "enhanced"),
    "skip": ("Skip current task", "enhanced"),
    "clear": ("Clear all queued tasks", "enhanced"),
}

def extract_commands_from_bot():
    """Extract registered commands from bot.py"""
    with open(BOT_FILE, 'r') as f:
        content = f.read()
    
    # Find CommandHandler registrations
    pattern = r'CommandHandler\("(\w+)"'
    commands = re.findall(pattern, content)
    return commands

def generate_commands_table():
    """Generate markdown tables for commands"""
    commands = extract_commands_from_bot()
    
    core_cmds = []
    enhanced_cmds = []
    
    for cmd in commands:
        if cmd in COMMAND_DOCS:
            desc, category = COMMAND_DOCS[cmd]
            row = f"| `/{cmd}` | {desc} |"
            if category == "core":
                core_cmds.append(row)
            else:
                enhanced_cmds.append(row)
        else:
            print(f"Warning: Command '{cmd}' not documented in COMMAND_DOCS")
    
    core_table = "### Core Commands\n| Command | Description |\n|---------|-------------|\n" + "\n".join(core_cmds)
    enhanced_table = "### Enhanced Commands (This Fork)\n| Command | Description |\n|---------|-------------|\n" + "\n".join(enhanced_cmds)
    
    return f"{core_table}\n\n{enhanced_table}"

def update_readme():
    """Update the Commands section in README"""
    with open(README_FILE, 'r') as f:
        readme = f.read()
    
    # Find and replace Commands section
    pattern = r'(## Commands\n\n).*?((?=\n## )|$)'
    new_commands = generate_commands_table()
    
    # Check if section exists
    if '## Commands' not in readme:
        print("Warning: ## Commands section not found in README")
        return False
    
    # Replace
    new_readme = re.sub(
        pattern,
        f'\\1{new_commands}\n\n',
        readme,
        flags=re.DOTALL
    )
    
    if new_readme != readme:
        with open(README_FILE, 'w') as f:
            f.write(new_readme)
        print("README.md updated with latest commands")
        return True
    else:
        print("README.md already up to date")
        return False

if __name__ == "__main__":
    update_readme()
