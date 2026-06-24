#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt

export LLM_DEMO_MODE="${LLM_DEMO_MODE:-mock_success}"
export LLM_MODEL="${LLM_MODEL:-deepseek-v4-flash}"

"$PYTHON_BIN" -m json.tool uipath-workflows/http-request-bodies/triage-po-1001.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/http-request-bodies/triage-po-1002.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/http-request-bodies/triage-po-1003.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/http-request-bodies/validation-request.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/http-request-bodies/validation-failed-request.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/http-request-bodies/api-mode-request.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/expected-outputs/triage-po-1001-response.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/expected-outputs/triage-po-1002-response.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/expected-outputs/triage-po-1003-response.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/expected-outputs/validation-response.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/expected-outputs/validation-failed-response.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/expected-outputs/api-mode-response.json >/dev/null
"$PYTHON_BIN" -m json.tool uipath-workflows/expected-outputs/final-case-output.json >/dev/null
"$PYTHON_BIN" -m json.tool generated-artifacts/request_purchase_order_approval/modernization-plan.json >/dev/null
"$PYTHON_BIN" -m json.tool generated-artifacts/request_purchase_order_approval/openapi-candidate.json >/dev/null

"$PYTHON_BIN" -m pytest mock-legacy-erp reasoning-agent generated-api-facade validation-suite
