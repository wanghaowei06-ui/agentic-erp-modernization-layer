#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/run"
UVICORN_BIN="$ROOT_DIR/.venv/bin/uvicorn"
mkdir -p "$RUN_DIR"

if [[ ! -x "$UVICORN_BIN" ]]; then
  UVICORN_BIN="$(command -v uvicorn)"
fi

start_service() {
  local name="$1"
  local port="$2"
  local dir="$3"
  local log_file="$RUN_DIR/${name}.log"
  local pid_file="$RUN_DIR/${name}.pid"

  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "$name already running on port $port with PID $(cat "$pid_file")"
    return
  fi

  cd "$ROOT_DIR/$dir"
  nohup setsid "$UVICORN_BIN" app.main:app --host 0.0.0.0 --port "$port" >"$log_file" 2>&1 &
  echo $! >"$pid_file"
  cd "$ROOT_DIR"
  echo "Started $name on http://localhost:$port"
}

start_service "mock-legacy-erp" 8000 "mock-legacy-erp"
start_service "reasoning-agent" 8001 "reasoning-agent"
start_service "generated-api-facade" 8002 "generated-api-facade"
start_service "validation-suite" 8003 "validation-suite"

echo
echo "Windows/UiPath URLs:"
echo "  http://localhost:8000"
echo "  http://localhost:8001"
echo "  http://localhost:8002"
echo "  http://localhost:8003"
echo
echo "Logs are in $RUN_DIR"
