#!/usr/bin/env bash
# Start llama.cpp server for Qwen3.6-35B (OpenAI-compatible API on :8080)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$([[ -f "$ROOT/.env" ]] && echo "$ROOT/.env" || echo "$ROOT/.env.example")}"
PID_FILE="${PID_FILE:-$ROOT/logs/llama-server.pid}"
LOG_FILE="${LOG_FILE:-$ROOT/logs/llama-server.log}"

# Optional CLI overrides (--agent-model-path / --agent-model) arrive via subprocess env.
# Preserve them before sourcing .env so stale MODEL_PATH in .env cannot clobber.
_CALLER_MODEL_PATH="${MODEL_PATH:-}"
_CALLER_MODEL_ALIAS="${MODEL_ALIAS:-}"
_CALLER_ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a && source "$ENV_FILE" && set +a
fi

if [[ -n "$_CALLER_MODEL_PATH" ]]; then
  MODEL_PATH="$_CALLER_MODEL_PATH"
fi
if [[ -n "$_CALLER_MODEL_ALIAS" ]]; then
  MODEL_ALIAS="$_CALLER_MODEL_ALIAS"
fi
if [[ -n "$_CALLER_ANTHROPIC_MODEL" ]]; then
  ANTHROPIC_MODEL="$_CALLER_ANTHROPIC_MODEL"
fi
unset _CALLER_MODEL_PATH _CALLER_MODEL_ALIAS _CALLER_ANTHROPIC_MODEL

LLAMA_CPP_DIR="${LLAMA_CPP_DIR:-}"
LLAMA_SERVER="${LLAMA_SERVER:-$LLAMA_CPP_DIR/build/bin/llama-server}"
MODEL_PATH="${MODEL_PATH:-}"
SLOT_SAVE_PATH="${SLOT_SAVE_PATH:-$ROOT/logs/slots}"
LLAMA_HOST="${LLAMA_HOST:-127.0.0.1}"
LLAMA_PORT="${LLAMA_PORT:-8080}"

# Server tuning (override in .env when switching models)
LLAMA_CTX_SIZE="${LLAMA_CTX_SIZE:-42000}"
LLAMA_THREADS="${LLAMA_THREADS:-12}"
# GPU layers offloaded to VRAM (-ngl). Set 0 for CPU-only; 999 = offload all layers.
LLAMA_NGL="${LLAMA_NGL:-999}"
# Auto-fit model + KV cache to available VRAM (--fit on|off).
#LLAMA_FIT="${LLAMA_FIT:-on}"
LLAMA_FLASH_ATTN="${LLAMA_FLASH_ATTN:-on}"
# Prefer mmap without mlock (evictable pages; lower resident RAM than mmap+mlock).
LLAMA_LOAD_MODE="${LLAMA_LOAD_MODE:-mmap}"
# Partial-prefix KV reuse via cache shifting (tokens). Set 0 to disable.
LLAMA_CACHE_REUSE="${LLAMA_CACHE_REUSE:-256}"
# TurboQuant KV cache types (turboquant fork): f16 | q8_0 | turbo2 | turbo3 | turbo4
# Requires flash-attn (auto-enabled by turbo types if off). See llama-cpp-turboquant/docs/KV-cache-quantization.md
LLAMA_CACHE_TYPE_K="${LLAMA_CACHE_TYPE_K:-turbo3}"
LLAMA_CACHE_TYPE_V="${LLAMA_CACHE_TYPE_V:-turbo3}"
# Server defaults; shared HF sampler knobs (constant across plan/coder/fix).
# Per-request temperature + presence_penalty are set by UnitTest_gen ADK agents (openai SDK).
LLAMA_TEMP="${LLAMA_TEMP:-0.7}"
LLAMA_TOP_P="${LLAMA_TOP_P:-0.8}"
LLAMA_TOP_K="${LLAMA_TOP_K:-20}"
LLAMA_MIN_P="${LLAMA_MIN_P:-0.0}"
LLAMA_REPEAT_PENALTY="${LLAMA_REPEAT_PENALTY:-1.0}"
LLAMA_SEED="${LLAMA_SEED:-42}"
# Server LLAMA_REASONING=off; plan agent enables thinking per-request via Python proxy
# (TESTGEN_PLAN_ENABLE_THINKING=1, budget 256); coder/fix forced off per request.
LLAMA_REASONING="${LLAMA_REASONING:-off}"
LLAMA_REASONING_FORMAT="${LLAMA_REASONING_FORMAT:-deepseek}"
# Tool-agent default: MTP + ngram-cache (MoE-safe n_max=2). Set LLAMA_SPEC_TYPE=none to disable.
LLAMA_SPEC_TYPE="${LLAMA_SPEC_TYPE:-draft-mtp,ngram-cache}"
LLAMA_SPEC_DRAFT_N_MAX="${LLAMA_SPEC_DRAFT_N_MAX:-2}"
LLAMA_SPEC_DRAFT_P_MIN="${LLAMA_SPEC_DRAFT_P_MIN:-0.1}"
LLAMA_DRAFT_MODEL="${LLAMA_DRAFT_MODEL:-}"
LLAMA_LOOKUP_CACHE_DYNAMIC="${LLAMA_LOOKUP_CACHE_DYNAMIC:-$ROOT/logs/ngram-cache-dynamic.bin}"
LLAMA_LOOKUP_CACHE_STATIC="${LLAMA_LOOKUP_CACHE_STATIC:-}"
# Optional patched Jinja template (--chat-template-file). Relative paths resolve from AgenticLLM/.
LLAMA_CHAT_TEMPLATE_FILE="${LLAMA_CHAT_TEMPLATE_FILE:-}"

mkdir -p "$(dirname "$PID_FILE")" "$(dirname "$LOG_FILE")" "$SLOT_SAVE_PATH"

if [[ ! -x "$LLAMA_SERVER" ]]; then
  echo "error: llama-server not found or not executable: $LLAMA_SERVER" >&2
  exit 1
fi

if [[ ! -f "$MODEL_PATH" ]]; then
  echo "error: model not found: $MODEL_PATH" >&2
  exit 1
fi

if [[ -n "$LLAMA_CHAT_TEMPLATE_FILE" ]]; then
  if [[ "$LLAMA_CHAT_TEMPLATE_FILE" != /* ]]; then
    LLAMA_CHAT_TEMPLATE_FILE="$ROOT/$LLAMA_CHAT_TEMPLATE_FILE"
  fi
  if [[ ! -f "$LLAMA_CHAT_TEMPLATE_FILE" ]]; then
    echo "error: LLAMA_CHAT_TEMPLATE_FILE not found: $LLAMA_CHAT_TEMPLATE_FILE" >&2
    exit 1
  fi
fi

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE")"
  if kill -0 "$old_pid" 2>/dev/null; then
    echo "llama-server already running (pid $old_pid)"
    echo "  health: http://${LLAMA_HOST}:${LLAMA_PORT}/health"
    if "$ROOT/scripts/health-check.sh" --wait 600; then
      echo "Server is ready."
      "$ROOT/scripts/health-check.sh" --models
      exit 0
    fi
    echo "warning: existing pid $old_pid is not healthy; restarting..." >&2
    "$ROOT/scripts/stop-llama-server.sh" || true
  fi
  rm -f "$PID_FILE"
fi

echo "Starting llama-server..."
echo "  model: $MODEL_PATH"
echo "  log:   $LOG_FILE"
echo "  api:   http://${LLAMA_HOST}:${LLAMA_PORT}/v1"
echo "  load:  ${LLAMA_LOAD_MODE}"
echo "  ngl:   ${LLAMA_NGL}"
#echo "  fit:   ${LLAMA_FIT}"
echo "  kv:    K=${LLAMA_CACHE_TYPE_K} V=${LLAMA_CACHE_TYPE_V}"
echo "  spec:  ${LLAMA_SPEC_TYPE} (draft-n-max=${LLAMA_SPEC_DRAFT_N_MAX}, p-min=${LLAMA_SPEC_DRAFT_P_MIN})"
echo "  sample: temp=${LLAMA_TEMP} top_p=${LLAMA_TOP_P} top_k=${LLAMA_TOP_K} min_p=${LLAMA_MIN_P} repeat_penalty=${LLAMA_REPEAT_PENALTY}"
if [[ -n "$LLAMA_CHAT_TEMPLATE_FILE" ]]; then
  echo "  chat template: $LLAMA_CHAT_TEMPLATE_FILE"
fi

SERVER_ARGS=(
  -m "$MODEL_PATH"
  --ctx-size "$LLAMA_CTX_SIZE"
  -ngl "$LLAMA_NGL"
  --flash-attn "$LLAMA_FLASH_ATTN"
  --cache-type-k "$LLAMA_CACHE_TYPE_K"
  --cache-type-v "$LLAMA_CACHE_TYPE_V"
  --temp "$LLAMA_TEMP"
  --top-p "$LLAMA_TOP_P"
  --top-k "$LLAMA_TOP_K"
  --min-p "$LLAMA_MIN_P"
  --repeat-penalty "$LLAMA_REPEAT_PENALTY"
  --seed "$LLAMA_SEED"
  --parallel 1
  --load-mode "$LLAMA_LOAD_MODE"
  --cache-reuse "$LLAMA_CACHE_REUSE"
  --slots
  --threads "$LLAMA_THREADS"
  --cont-batching
  --timeout 300
  --port "$LLAMA_PORT"
  --host "$LLAMA_HOST"
  --tools all
  --jinja
  --metrics
  --slot-save-path "$SLOT_SAVE_PATH"
)

if [[ -n "$LLAMA_CHAT_TEMPLATE_FILE" ]]; then
  SERVER_ARGS+=(--chat-template-file "$LLAMA_CHAT_TEMPLATE_FILE")
fi

#if [[ -n "$LLAMA_FIT" && "$LLAMA_FIT" != "off" ]]; then
#  SERVER_ARGS+=(--fit "$LLAMA_FIT")
#fi

if [[ -n "$LLAMA_DRAFT_MODEL" ]]; then
  SERVER_ARGS+=(-md "$LLAMA_DRAFT_MODEL")
fi

if [[ -n "$LLAMA_SPEC_TYPE" && "$LLAMA_SPEC_TYPE" != "none" ]]; then
  SERVER_ARGS+=(--spec-type "$LLAMA_SPEC_TYPE")
  SERVER_ARGS+=(--spec-draft-n-max "$LLAMA_SPEC_DRAFT_N_MAX")
  SERVER_ARGS+=(--spec-draft-p-min "$LLAMA_SPEC_DRAFT_P_MIN")
  if [[ -n "$LLAMA_LOOKUP_CACHE_DYNAMIC" ]]; then
    mkdir -p "$(dirname "$LLAMA_LOOKUP_CACHE_DYNAMIC")"
    # llama.cpp aborts if -lcd path is missing; empty file = empty ngram cache (valid).
    if [[ ! -f "$LLAMA_LOOKUP_CACHE_DYNAMIC" ]]; then
      : >"$LLAMA_LOOKUP_CACHE_DYNAMIC"
      echo "  ngram dynamic cache: created empty $LLAMA_LOOKUP_CACHE_DYNAMIC"
    fi
    SERVER_ARGS+=(--lookup-cache-dynamic "$LLAMA_LOOKUP_CACHE_DYNAMIC")
    echo "  ngram dynamic cache: $LLAMA_LOOKUP_CACHE_DYNAMIC"
  fi
  if [[ -n "$LLAMA_LOOKUP_CACHE_STATIC" ]]; then
    if [[ ! -f "$LLAMA_LOOKUP_CACHE_STATIC" ]]; then
      echo "error: LLAMA_LOOKUP_CACHE_STATIC set but file missing: $LLAMA_LOOKUP_CACHE_STATIC" >&2
      exit 1
    fi
    SERVER_ARGS+=(--lookup-cache-static "$LLAMA_LOOKUP_CACHE_STATIC")
    echo "  ngram static cache:  $LLAMA_LOOKUP_CACHE_STATIC"
  fi
fi

if [[ -n "$LLAMA_REASONING" && "$LLAMA_REASONING" != "off" ]]; then
  SERVER_ARGS+=(--reasoning "$LLAMA_REASONING")
  if [[ -n "$LLAMA_REASONING_FORMAT" ]]; then
    SERVER_ARGS+=(--reasoning-format "$LLAMA_REASONING_FORMAT")
  fi
fi

if [[ -n "${MODEL_ALIAS:-}" ]]; then
  SERVER_ARGS+=(--alias "$MODEL_ALIAS")
fi

nohup "$LLAMA_SERVER" "${SERVER_ARGS[@]}" >>"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"

echo "llama-server started (pid $(cat "$PID_FILE"))"
echo "Waiting for server to become ready (model load can take several minutes)..."

if "$ROOT/scripts/health-check.sh" --wait 600; then
  echo "Server is ready."
  "$ROOT/scripts/health-check.sh" --models
else
  echo "warning: server did not become ready within timeout. Check $LOG_FILE" >&2
  exit 1
fi
