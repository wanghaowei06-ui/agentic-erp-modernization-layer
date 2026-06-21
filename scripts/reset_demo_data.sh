#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

RESET_DEMO_ROOT="$ROOT_DIR" "$PYTHON_BIN" - <<'PY'
import os
import sys
from pathlib import Path

root = Path(os.environ["RESET_DEMO_ROOT"])

for service_dir in ["mock-legacy-erp", "generated-api-facade"]:
    sys.path.insert(0, str(root / service_dir))
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    from app.db import init_db

    init_db()
    print(f"reset {service_dir}")
    sys.path.pop(0)

print("demo data reset for PO-1001, PO-1002, PO-1003, and local cloned test-case state")
PY
