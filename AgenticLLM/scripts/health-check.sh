#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$([[ -f "$ROOT/.env" ]] && echo "$ROOT/.env" || echo "$ROOT/.env.example")}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a && source "$ENV_FILE" && set +a
fi

LLAMA_HOST="${LLAMA_HOST:-127.0.0.1}"
LLAMA_PORT="${LLAMA_PORT:-8080}"
BASE_URL="http://${LLAMA_HOST}:${LLAMA_PORT}"

WAIT=0
TIMEOUT=30

usage() {
  cat <<EOF
Usage: $(basename "$0") [--wait SECONDS] [--models]

  --wait SECONDS   Poll until server is healthy (default: 0 = single check)
  --models         Print /v1/models response
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wait)
      WAIT=1
      TIMEOUT="${2:-30}"
      shift 2
      ;;
    --models)
      SHOW_MODELS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

check_health() {
  curl -sf "${BASE_URL}/health" >/dev/null 2>&1
}

if [[ "$WAIT" -eq 1 ]]; then
  echo "Waiting for ${BASE_URL}/health (timeout ${TIMEOUT}s)..."
  for i in $(seq 1 "$TIMEOUT"); do
    if check_health; then
      echo "healthy (${i}s)"
      break
    fi
    if [[ "$i" -eq "$TIMEOUT" ]]; then
      echo "not healthy after ${TIMEOUT}s" >&2
      exit 1
    fi
    sleep 1
  done
else
  if ! check_health; then
    echo "llama-server not healthy at ${BASE_URL}/health" >&2
    exit 1
  fi
  echo "llama-server healthy at ${BASE_URL}"
fi

if [[ "${SHOW_MODELS:-0}" -eq 1 ]]; then
  echo "--- /v1/models ---"
  curl -sf "${BASE_URL}/v1/models" | jq . 2>/dev/null || curl -sf "${BASE_URL}/v1/models"
fi
