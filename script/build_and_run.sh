#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
case "$MODE" in
  run|--verify|verify) ;;
  *)
    echo "usage: $0 [run|--verify]" >&2
    exit 2
    ;;
esac

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PYTHON="$ROOT_DIR/backend/venv/bin/python"
DATA_DIR="${VOICEBOX_DATA_DIR:-$HOME/Library/Application Support/sh.voicebox.app}"
MODELS_DIR="${VOICEBOX_MODELS_DIR:-$HOME/.cache/huggingface/hub}"
DEV_CONFIG="$ROOT_DIR/tauri/src-tauri/tauri.dev.conf.json"
PORT=17494
BACKEND_PID=""
TAURI_PID=""
APP_PID=""

cleanup() {
  status=$?
  trap - EXIT INT TERM HUP
  for pid in "$APP_PID" "$TAURI_PID" "$BACKEND_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
  exit "$status"
}
trap cleanup EXIT INT TERM HUP

[[ -x "$BACKEND_PYTHON" ]] || {
  echo "Missing backend virtualenv: $BACKEND_PYTHON (run 'just setup' first)." >&2
  exit 1
}
for command in bun curl lsof; do
  command -v "$command" >/dev/null || {
    echo "Missing required command: $command" >&2
    exit 1
  }
done

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null; then
  echo "Port $PORT is already in use; refusing to reuse or stop that process." >&2
  exit 1
fi

mkdir -p "$DATA_DIR"
export VOICEBOX_MODELS_DIR="$MODELS_DIR"
export VITE_VOICEBOX_SERVER_PORT="$PORT"

cd "$ROOT_DIR"
bun run setup:dev
"$BACKEND_PYTHON" -m backend.main --data-dir "$DATA_DIR" --port "$PORT" &
BACKEND_PID=$!

ready=false
for ((attempt = 0; attempt < 120; attempt++)); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    ready=true
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    wait "$BACKEND_PID" || true
    echo "Voicebox Dev backend exited before becoming ready." >&2
    exit 1
  fi
  sleep 1
done
[[ "$ready" == true ]] || {
  echo "Voicebox Dev backend did not become ready on port $PORT." >&2
  exit 1
}

cd "$ROOT_DIR/tauri"
TAURI_COMMAND=(bun run tauri dev --config "$DEV_CONFIG" --features dev-isolation)

if [[ "$MODE" == run ]]; then
  "${TAURI_COMMAND[@]}"
  exit
fi

command -v pgrep >/dev/null || {
  echo "Missing required command: pgrep" >&2
  exit 1
}
BEFORE_APP_PIDS=" $(pgrep -x voicebox 2>/dev/null | tr '\n' ' ' || true)"
"${TAURI_COMMAND[@]}" &
TAURI_PID=$!

for ((attempt = 0; attempt < 300; attempt++)); do
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    case "$BEFORE_APP_PIDS" in
      *" $pid "*) ;;
      *)
        APP_PID="$pid"
        echo "Voicebox Dev verified (PID $APP_PID, backend PID $BACKEND_PID)."
        exit
        ;;
    esac
  done < <(pgrep -x voicebox 2>/dev/null || true)
  if ! kill -0 "$TAURI_PID" 2>/dev/null; then
    wait "$TAURI_PID" || true
    echo "Tauri dev exited before Voicebox Dev started." >&2
    exit 1
  fi
  sleep 1
done

echo "Voicebox Dev process did not appear before the verification timeout." >&2
exit 1
