#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

rm -rf "$ROOT_DIR/memory-data"

assert_json() {
  local label="$1"
  local expected_expr="$2"
  shift 2
  local response
  response="$(curl --retry 10 --retry-connrefused --retry-delay 1 -sS "$@")"
  RESPONSE="$response" "$PYTHON_BIN" - "$expected_expr" <<'PY'
import json
import os
import sys

body = json.loads(os.environ["RESPONSE"])
expr = sys.argv[1]
if not eval(expr, {"body": body}, {"body": body}):
    print(json.dumps(body, indent=2))
    raise SystemExit(f"assertion failed: {expr}")
PY
  echo "[OK] $label"
}

assert_file() {
  local path="$1"
  test -f "$path"
}

assert_json "Mock ERP health" "body['status'] == 'ok'" http://localhost:8001/health

assert_json "Triage PO-1001 budget_exceeded" \
  "body['detected_exception_type'] == 'budget_exceeded'" \
  -X POST http://localhost:8002/triage \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "CASE-001",
    "po_id": "PO-1001",
    "amount": 18000,
    "budget_limit": 10000,
    "vendor_id": "V-203",
    "vendor_info_complete": true,
    "inventory_available": true,
    "erp_status": "Exception",
    "raw_exception_text": "Amount exceeds approved budget limit"
  }'

assert_json "Triage PO-1002 vendor_info_missing" \
  "body['detected_exception_type'] == 'vendor_info_missing'" \
  -X POST http://localhost:8002/triage \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "CASE-002",
    "po_id": "PO-1002",
    "amount": 6000,
    "budget_limit": 10000,
    "vendor_id": "",
    "vendor_info_complete": false,
    "inventory_available": true,
    "erp_status": "Exception",
    "raw_exception_text": "Vendor information missing"
  }'

assert_json "Triage PO-1003 inventory_shortage" \
  "body['detected_exception_type'] == 'inventory_shortage' and body['next_stage'] == 'CAPABILITY_GAP_DETECTED'" \
  -X POST http://localhost:8002/triage \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "CASE-003",
    "po_id": "PO-1003",
    "amount": 8500,
    "budget_limit": 10000,
    "vendor_id": "V-118",
    "vendor_info_complete": true,
    "inventory_available": false,
    "erp_status": "Exception",
    "raw_exception_text": "Inventory shortage"
  }'

assert_json "Generated API approval request" \
  "body['execution_mode'] == 'API' and body['status'] == 'PENDING_MANAGER_APPROVAL'" \
  -X POST http://localhost:8003/api/purchase-orders/PO-1001-API/approval-request \
  -H "Content-Type: application/json" \
  -d '{
    "approval_reason": "Amount exceeds budget limit",
    "manager_id": "MGR-001",
    "source_case_id": "CASE-001"
  }'

curl --retry 10 --retry-connrefused --retry-delay 1 -sS \
  -X POST http://localhost:8001/purchase-orders/PO-1001/request-approval \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "case_id=CASE-001" \
  --data-urlencode "approval_reason=Amount exceeds budget limit" \
  --data-urlencode "manager_id=MGR-001" >/dev/null

assert_json "Validation parity passed" \
  "body['rpa_api_parity_check'] == 'passed' and body['same_initial_state'] is True" \
  -X POST http://localhost:8004/validate/request-purchase-order-approval \
  -H "Content-Type: application/json" \
  -d '{"case_id": "CASE-001"}'

assert_json "Capability gap prepared" \
  "body['coverage_status'] == 'not_covered' and body['case_id'] == 'CASE-003'" \
  -X POST http://localhost:8004/capability-gaps/inventory-shortage >/dev/null

for file in \
  "$ROOT_DIR/memory/data/case_state_CASE-001.json" \
  "$ROOT_DIR/memory/data/case_timeline_CASE-001.json" \
  "$ROOT_DIR/memory/data/agent_decision_CASE-001.json" \
  "$ROOT_DIR/memory/data/human_approval_CASE-001.json" \
  "$ROOT_DIR/memory/data/rpa_trace_CASE-001.json" \
  "$ROOT_DIR/memory/data/validation_result_CASE-001.json" \
  "$ROOT_DIR/memory/data/capability_registry.json" \
  "$ROOT_DIR/memory/data/capability_gap_CASE-003.json"; do
  assert_file "$file"
done
echo "[OK] Memory artifacts generated"
echo "[OK] Capability gap recorded"

assert_json "RPA write-back memory recorded" \
  "any(event['event_type'] == 'RPA_WRITEBACK_COMPLETED' for event in body['events'])" \
  http://localhost:8004/memory/cases/CASE-001/timeline

assert_json "Memory timeline query available" \
  "body['case_id'] == 'CASE-001' and isinstance(body['events'], list)" \
  http://localhost:8004/memory/cases/CASE-001/timeline

assert_json "Memory timeline contains triage/validation/api events" \
  "all(item in {event['event_type'] for event in body['events']} for item in ['TRIAGE_COMPLETED', 'VALIDATION_COMPLETED', 'API_EXECUTION_COMPLETED'])" \
  http://localhost:8004/memory/cases/CASE-001/timeline

assert_json "Memory capabilities query available" \
  "len([cap for cap in body['capabilities'] if cap['status'] == 'trusted' and cap['validation_status'] == 'passed']) >= 2" \
  http://localhost:8004/memory/capabilities

assert_json "Memory gaps query available" \
  "any(gap['event_type'] == 'CAPABILITY_GAP_RECORDED' for gap in body['gaps'])" \
  http://localhost:8004/memory/gaps
