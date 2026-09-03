#!/usr/bin/env bash
# Install Claude Code CLI to ~/.local/bin (if missing).
set -euo pipefail

if [[ -x "$HOME/.local/bin/claude" ]] || command -v claude >/dev/null 2>&1; then
  echo "claude already installed: $(command -v claude 2>/dev/null || echo "$HOME/.local/bin/claude")"
  exit 0
fi

echo "Installing Claude Code (native installer)..."
# Official install path; see https://docs.anthropic.com/en/docs/claude-code/setup
curl -fsSL https://claude.ai/install.sh | bash

if [[ -x "$HOME/.local/bin/claude" ]] || command -v claude >/dev/null 2>&1; then
  echo "Installed: $(command -v claude 2>/dev/null || echo "$HOME/.local/bin/claude")"
  exit 0
fi

echo "error: install finished but claude binary not found on PATH" >&2
exit 1
