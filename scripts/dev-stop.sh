#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/run"

for name in mock-legacy-erp reasoning-agent generated-api-facade validation-suite; do
  pid_file="$RUN_DIR/${name}.pid"
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" || true
      echo "Stopped $name PID $pid"
    else
      echo "$name PID $pid is not running"
    fi
    rm -f "$pid_file"
  else
    echo "$name has no PID file"
  fi
done
