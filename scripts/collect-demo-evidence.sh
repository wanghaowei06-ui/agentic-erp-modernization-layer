#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/docs/evidence"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

mkdir -p "$OUT_DIR"
rm -rf "$ROOT_DIR/memory-data"

save_json() {
  local label="$1"
  local file="$2"
  shift 2
  local response
  if ! response="$(curl --retry 10 --retry-connrefused --retry-delay 1 -sS "$@")"; then
    echo "[FAIL] $label"
    return 1
  fi
  RESPONSE="$response" "$PYTHON_BIN" - "$OUT_DIR/$file" <<'PY'
import json
import os
import sys

body = json.loads(os.environ["RESPONSE"])
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(body, handle, indent=2)
    handle.write("\n")
PY
  echo "[OK] $label"
}

assert_saved_json() {
  local label="$1"
  local file="$2"
  local expected_expr="$3"
  if ! "$PYTHON_BIN" - "$OUT_DIR/$file" "$expected_expr" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    body = json.load(handle)

expr = sys.argv[2]
if not eval(expr, {"body": body}, {"body": body}):
    print(json.dumps(body, indent=2))
    raise SystemExit(f"assertion failed: {expr}")
PY
  then
    echo "[FAIL] $label"
    return 1
  fi
  echo "[OK] $label"
}

save_json "Mock ERP health" "health-8001.json" http://localhost:8001/health
save_json "Reasoning Agent health" "health-8002.json" http://localhost:8002/health
save_json "Generated API health" "health-8003.json" http://localhost:8003/health
save_json "Validation Suite health" "health-8004.json" http://localhost:8004/health
assert_saved_json "Health checks returned ok" "health-8001.json" "body['status'] == 'ok'"

save_json "Triage PO-1001" "triage-po-1001.json" \
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
assert_saved_json "Triage PO-1001 is budget_exceeded" "triage-po-1001.json" \
  "body['detected_exception_type'] == 'budget_exceeded'"

curl --retry 10 --retry-connrefused --retry-delay 1 -sS \
  -X POST http://localhost:8001/purchase-orders/PO-1001/request-approval \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "case_id=CASE-001" \
  --data-urlencode "approval_reason=Amount exceeds budget limit" \
  --data-urlencode "manager_id=MGR-001" \
  >"$OUT_DIR/rpa-writeback-po-1001.html"
echo "[OK] RPA write-back HTML"

save_json "Generated API approval" "generated-api-approval.json" \
  -X POST http://localhost:8003/api/purchase-orders/PO-1001-API/approval-request \
  -H "Content-Type: application/json" \
  -d '{
    "approval_reason": "Amount exceeds budget limit",
    "manager_id": "MGR-001",
    "source_case_id": "CASE-001"
  }'
assert_saved_json "Generated API executed in API mode" "generated-api-approval.json" \
  "body['execution_mode'] == 'API' and body['status'] == 'PENDING_MANAGER_APPROVAL'"

save_json "Validation result" "validation-result.json" \
  -X POST http://localhost:8004/validate/request-purchase-order-approval \
  -H "Content-Type: application/json" \
  -d '{"case_id": "CASE-001"}'
assert_saved_json "Validation wrote passed result" "validation-result.json" \
  "body['rpa_api_parity_check'] == 'passed' and body['same_initial_state'] is True"

save_json "Capability gap" "capability-gap-case-003.json" \
  -X POST http://localhost:8004/capability-gaps/inventory-shortage
assert_saved_json "Capability gap recorded for CASE-003" "capability-gap-case-003.json" \
  "body['case_id'] == 'CASE-003' and body['coverage_status'] == 'not_covered'"

save_json "Memory case summary" "memory-case-001.json" \
  http://localhost:8004/memory/cases/CASE-001
save_json "Memory timeline" "memory-timeline-case-001.json" \
  http://localhost:8004/memory/cases/CASE-001/timeline
assert_saved_json "Memory timeline contains triage/writeback/validation/api" "memory-timeline-case-001.json" \
  "all(item in {event['event_type'] for event in body['events']} for item in ['TRIAGE_COMPLETED', 'RPA_WRITEBACK_COMPLETED', 'VALIDATION_COMPLETED', 'API_EXECUTION_COMPLETED'])"
save_json "Memory decisions" "memory-decisions-case-001.json" \
  http://localhost:8004/memory/decisions/CASE-001
assert_saved_json "Memory decisions include triage" "memory-decisions-case-001.json" \
  "any(event['event_type'] == 'TRIAGE_COMPLETED' for event in body['events'])"
save_json "Memory capabilities" "memory-capabilities.json" \
  http://localhost:8004/memory/capabilities
assert_saved_json "Memory capabilities include trusted entries" "memory-capabilities.json" \
  "len([cap for cap in body['capabilities'] if cap['status'] == 'trusted' and cap['validation_status'] == 'passed']) >= 2"
save_json "Memory gaps" "memory-gaps.json" \
  http://localhost:8004/memory/gaps
assert_saved_json "Memory gaps include capability gap event" "memory-gaps.json" \
  "any(gap['event_type'] == 'CAPABILITY_GAP_RECORDED' for gap in body['gaps'])"

COMMIT_SHA="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
COMMIT_STATUS="$(git -C "$ROOT_DIR" status --short 2>/dev/null || true)"
if [[ -n "$COMMIT_STATUS" ]]; then
  COMMIT_SHA="${COMMIT_SHA}-dirty"
fi

"$PYTHON_BIN" - "$OUT_DIR" "$COMMIT_SHA" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

out_dir = Path(sys.argv[1])
commit_sha = sys.argv[2]
evidence_files = sorted(
    path.name
    for path in out_dir.iterdir()
    if path.is_file() and path.name != "manifest.json"
)

manifest = {
    "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "commit_sha": commit_sha,
    "release_candidate": "hard-mvp-automation-memory-rc1",
    "service_ports": {
        "mock_legacy_erp": 8001,
        "reasoning_agent": 8002,
        "generated_api_facade": 8003,
        "validation_suite": 8004,
    },
    "smoke_test_status": "passed_by_latest_smoke_test_required",
    "pytest_summary": "run make demo-check or individual pytest commands before submission",
    "demo_case_id": "CASE-001",
    "primary_po_id": "PO-1001",
    "capability_gap_case_id": "CASE-003",
    "capability_gap_po_id": "PO-1003",
    "memory_events_expected": [
        "TRIAGE_COMPLETED",
        "RPA_WRITEBACK_COMPLETED",
        "VALIDATION_COMPLETED",
        "API_EXECUTION_COMPLETED",
    ],
    "memory_query_urls": [
        "http://localhost:8004/memory/cases/CASE-001",
        "http://localhost:8004/memory/cases/CASE-001/timeline",
        "http://localhost:8004/memory/decisions/CASE-001",
        "http://localhost:8004/memory/capabilities",
        "http://localhost:8004/memory/gaps",
    ],
    "evidence_files": evidence_files,
}

with open(out_dir / "manifest.json", "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2)
    handle.write("\n")
PY
echo "[OK] Evidence manifest generated"

echo "Evidence written to $OUT_DIR"
