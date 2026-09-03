#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${PID_FILE:-$ROOT/logs/llama-server.pid}"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No pid file found ($PID_FILE). Server may not be running."
  exit 0
fi

pid="$(cat "$PID_FILE")"
if kill -0 "$pid" 2>/dev/null; then
  echo "Stopping llama-server (pid $pid)..."
  kill "$pid"
  for _ in $(seq 1 30); do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "Force killing llama-server..."
    kill -9 "$pid" 2>/dev/null || true
  fi
  echo "Stopped."
else
  echo "Process $pid is not running."
fi

rm -f "$PID_FILE"
