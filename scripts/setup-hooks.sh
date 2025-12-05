#!/bin/bash
# Setup git hooks for auto-updating README

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_DIR="$PROJECT_DIR/.git/hooks"

echo "Installing git hooks..."

# Copy pre-commit hook
cp "$PROJECT_DIR/.github/hooks/pre-commit" "$HOOKS_DIR/pre-commit"
chmod +x "$HOOKS_DIR/pre-commit"

echo "Done! Pre-commit hook installed."
echo "README will auto-update when bot.py changes."
