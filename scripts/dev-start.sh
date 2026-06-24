#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/run"
UVICORN_BIN="$ROOT_DIR/.venv/bin/uvicorn"
mkdir -p "$RUN_DIR"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

if [[ ! -x "$UVICORN_BIN" ]]; then
  UVICORN_BIN="$(command -v uvicorn)"
fi

start_service() {
  local name="$1"
  local port="$2"
  local dir="$3"
  local app_module="$4"
  local log_file="$RUN_DIR/${name}.log"
  local pid_file="$RUN_DIR/${name}.pid"

  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "$name already running on http://localhost:$port with PID $(cat "$pid_file")"
    return
  fi

  (
    cd "$ROOT_DIR/$dir"
    nohup setsid "$UVICORN_BIN" "$app_module" --host 0.0.0.0 --port "$port" >"$log_file" 2>&1 &
    echo $! >"$pid_file"
  )
  echo "Started $name on http://localhost:$port (host 0.0.0.0, log $log_file)"
}

start_service "mock-legacy-erp" 8001 "mock-legacy-erp" "app.main:app"
start_service "reasoning-agent" 8002 "reasoning-agent" "app.main:app"
start_service "generated-api-facade" 8003 "generated-api-facade" "app.main:app"
start_service "validation-suite" 8004 "validation-suite" "app.main:app"

echo
echo "Windows/UiPath URLs:"
echo "  Mock Legacy ERP:      http://localhost:8001"
echo "  Reasoning Agent:      http://localhost:8002"
echo "  Generated API Facade: http://localhost:8003"
echo "  Validation Suite:     http://localhost:8004"
echo
echo "Services bind to 0.0.0.0 for WSL/Windows browser and UiPath HTTP Request access."
echo "Logs and PID files are in $RUN_DIR"
