#!/usr/bin/env python3
"""
Auto-update README.md with commands AND features extracted from bot.py
Run this before committing to keep docs in sync.
"""
import re
import os
import ast

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
    "projects": ("List all auto-tracked projects", "enhanced"),
    "proj": ("Switch project (auto-tracked or manual shortcut)", "enhanced"),
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


def extract_features_from_bot():
    """Extract BOT_FEATURES dict from bot.py"""
    with open(BOT_FILE, 'r') as f:
        content = f.read()
    
    # Find BOT_FEATURES block - match balanced braces
    start = content.find('BOT_FEATURES = {')
    if start == -1:
        print("Warning: BOT_FEATURES not found in bot.py")
        return {}
    
    # Find matching closing brace
    brace_count = 0
    end = start + len('BOT_FEATURES = ')
    for i, char in enumerate(content[end:], end):
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                end = i + 1
                break
    
    try:
        features_str = content[start + len('BOT_FEATURES = '):end]
        features = ast.literal_eval(features_str)
        return features
    except Exception as e:
        print(f"Warning: Could not parse BOT_FEATURES: {e}")
        return {}


def generate_features_list():
    """Generate markdown list of features"""
    features = extract_features_from_bot()
    if not features:
        return None
    
    lines = []
    for key, feat in features.items():
        emoji = feat.get('emoji', '•')
        name = feat.get('name', key)
        desc = feat.get('desc', '')
        lines.append(f"- {emoji} **{name}** - {desc}")
    
    return "\n".join(lines)

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
    """Update Commands and Features sections in README"""
    with open(README_FILE, 'r') as f:
        readme = f.read()
    
    updated = False
    
    # Update Commands section
    commands_pattern = r'(## Commands\n\n).*?((?=\n## )|$)'
    new_commands = generate_commands_table()
    
    if '## Commands' in readme:
        new_readme = re.sub(
            commands_pattern,
            f'\\1{new_commands}\n\n',
            readme,
            flags=re.DOTALL
        )
        if new_readme != readme:
            readme = new_readme
            updated = True
            print("README.md: Commands section updated")
    else:
        print("Warning: ## Commands section not found in README")
    
    # Update Enhanced Features section
    features_list = generate_features_list()
    if features_list:
        features_pattern = r'(### Enhanced Features \(This Fork\)\n).*?((?=\n## |\n### [^E])|$)'
        if '### Enhanced Features' in readme:
            new_readme = re.sub(
                features_pattern,
                f'\\1{features_list}\n\n',
                readme,
                flags=re.DOTALL
            )
            if new_readme != readme:
                readme = new_readme
                updated = True
                print("README.md: Enhanced Features section updated")
        else:
            print("Warning: ### Enhanced Features section not found in README")
    
    if updated:
        with open(README_FILE, 'w') as f:
            f.write(readme)
        print("README.md saved")
        return True
    else:
        print("README.md already up to date")
        return False

if __name__ == "__main__":
    update_readme()
