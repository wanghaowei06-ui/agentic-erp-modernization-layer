#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python -m json.tool uipath-workflows/http-request-bodies/triage-po-1001.json >/dev/null
python -m json.tool uipath-workflows/http-request-bodies/triage-po-1002.json >/dev/null
python -m json.tool uipath-workflows/http-request-bodies/triage-po-1003.json >/dev/null
python -m json.tool uipath-workflows/http-request-bodies/validation-request.json >/dev/null
python -m json.tool uipath-workflows/http-request-bodies/validation-failed-request.json >/dev/null
python -m json.tool uipath-workflows/http-request-bodies/api-mode-request.json >/dev/null
python -m json.tool uipath-workflows/expected-outputs/triage-po-1001-response.json >/dev/null
python -m json.tool uipath-workflows/expected-outputs/triage-po-1002-response.json >/dev/null
python -m json.tool uipath-workflows/expected-outputs/triage-po-1003-response.json >/dev/null
python -m json.tool uipath-workflows/expected-outputs/validation-response.json >/dev/null
python -m json.tool uipath-workflows/expected-outputs/validation-failed-response.json >/dev/null
python -m json.tool uipath-workflows/expected-outputs/api-mode-response.json >/dev/null
python -m json.tool uipath-workflows/expected-outputs/final-case-output.json >/dev/null

python -m pytest mock-legacy-erp reasoning-agent generated-api-facade validation-suite
