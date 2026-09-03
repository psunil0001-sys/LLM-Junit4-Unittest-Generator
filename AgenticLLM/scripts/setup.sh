#!/usr/bin/env bash
# One-shot setup: install Claude Code CLI, then print next steps.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Installing Claude Code CLI"
"$ROOT/scripts/install-claude.sh"

echo
echo "==> Setup complete (AgenticLLM)"
echo
echo "1. Start the local model server (loads ~39GB model, may take several minutes):"
echo "     $ROOT/scripts/start-llama-server.sh"
echo
echo "2. Launch Claude Code against the shared stack:"
echo "     $ROOT/bin/claude-local"
echo
echo "Optional: add to PATH"
echo "  export PATH=\"$ROOT/bin:\$HOME/.local/bin:\$PATH\""
