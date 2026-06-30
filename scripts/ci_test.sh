#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_CMD="$PYTHON_BIN"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_CMD="$ROOT_DIR/.venv/bin/python"
else
  echo "No Python virtualenv found. Create one first:" >&2
  echo "  python3 -m venv .venv" >&2
  echo "  .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if ! "$PYTHON_CMD" - <<'PY' >/dev/null 2>&1
import fastapi
import pytest
PY
then
  "$PYTHON_CMD" -m pip install -r requirements.txt
fi

export LLM_DEMO_MODE="${LLM_DEMO_MODE:-mock_success}"
export LLM_MODEL="${LLM_MODEL:-deepseek-v4-flash}"

"$PYTHON_CMD" -m json.tool uipath-workflows/http-request-bodies/case-intake-route-po-1000.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/http-request-bodies/case-intake-route-po-1001.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/http-request-bodies/case-intake-route-po-1002.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/http-request-bodies/case-intake-route-po-1003.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/http-request-bodies/case-intake-route-po-1004.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/http-request-bodies/validation-request.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/http-request-bodies/validation-failed-request.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/http-request-bodies/api-mode-request.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/expected-outputs/case-intake-route-po-1000-response.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/expected-outputs/case-intake-route-po-1001-response.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/expected-outputs/case-intake-route-po-1002-response.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/expected-outputs/case-intake-route-po-1003-response.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/expected-outputs/case-intake-route-po-1004-response.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/expected-outputs/validation-response.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/expected-outputs/validation-failed-response.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/expected-outputs/api-mode-response.json >/dev/null
"$PYTHON_CMD" -m json.tool uipath-workflows/expected-outputs/final-case-output.json >/dev/null
"$PYTHON_CMD" -m json.tool generated-artifacts/request_purchase_order_approval/modernization-plan.json >/dev/null
"$PYTHON_CMD" -m json.tool generated-artifacts/request_purchase_order_approval/openapi-candidate.json >/dev/null

"$PYTHON_CMD" -m pytest mock-legacy-erp reasoning-agent generated-api-facade validation-suite
