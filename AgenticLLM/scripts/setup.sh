#!/usr/bin/env bash
# One-shot setup notes for the local OpenAI-compatible llama stack.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> AgenticLLM setup"
echo
echo "1. Copy env and set MODEL_PATH / MODEL_ALIAS (or ANTHROPIC_MODEL):"
echo "     cp $ROOT/.env.example $ROOT/.env"
echo
echo "2. Start the local model server (loads GGUF; may take several minutes):"
echo "     $ROOT/scripts/start-llama-server.sh"
echo
echo "3. Health check:"
echo "     $ROOT/scripts/health-check.sh --models"
echo
echo "4. Install Python deps for UnitTest_gen (google-adk + openai):"
echo "     pip install -r $(cd "$ROOT/.." && pwd)/requirements.txt"
echo
echo "Agents (planner/coder/fixer) talk to http://127.0.0.1:8080/v1 via the openai SDK."
echo "Remote vLLM: export TESTGEN_LLM_BACKEND=vllm TESTGEN_VLLM_BASE_URL=... TESTGEN_VLLM_API_KEY=..."
