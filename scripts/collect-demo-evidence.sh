#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/docs/evidence"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

mkdir -p "$OUT_DIR"

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
assert_saved_json "Health checks returned ok" "health-8002.json" "body['status'] == 'ok'"

save_json "Enterprise context" "company-context.json" http://localhost:8002/company-context
assert_saved_json "Enterprise context includes finance, sales, operations" "company-context.json" \
  "body['company']['finance_policy']['requires_manager_approval_above_budget'] is True and body['company']['sales_context']['quarter_end_revenue_pressure'] is True and body['company']['operations_context']['inventory_risk_tolerance'] == 'low'"

save_json "Route PO-1000 deterministic" "case-intake-route-po-1000.json" \
  -X POST http://localhost:8002/case-intake/route \
  -H "Content-Type: application/json" \
  -d @"$ROOT_DIR/uipath-workflows/http-request-bodies/case-intake-route-po-1000.json"
assert_saved_json "PO-1000 is deterministic" "case-intake-route-po-1000.json" \
  "body['agent_required'] is False and body['precheck_decision_source'] == 'deterministic_rule'"

save_json "Route PO-1001 with enterprise context" "case-intake-route-po-1001.json" \
  -X POST http://localhost:8002/case-intake/route \
  -H "Content-Type: application/json" \
  -d @"$ROOT_DIR/uipath-workflows/http-request-bodies/case-intake-route-po-1001.json"
assert_saved_json "PO-1001 uses agent context and proof" "case-intake-route-po-1001.json" \
  "body['agent_context_used'] is True and body['company_context_reference']['finance_policy_used'] is True and body['llm_validation_proof']['llm_invocation_verified'] is True and body['recommended_erp_action']['action_id'] == 'CREATE_WEB_APPROVAL_TASK'"

save_json "Route PO-1002 vendor wait" "case-intake-route-po-1002.json" \
  -X POST http://localhost:8002/case-intake/route \
  -H "Content-Type: application/json" \
  -d @"$ROOT_DIR/uipath-workflows/http-request-bodies/case-intake-route-po-1002.json"
save_json "Route PO-1003 capability gap" "case-intake-route-po-1003.json" \
  -X POST http://localhost:8002/case-intake/route \
  -H "Content-Type: application/json" \
  -d @"$ROOT_DIR/uipath-workflows/http-request-bodies/case-intake-route-po-1003.json"
save_json "Route PO-1004 manual investigation" "case-intake-route-po-1004.json" \
  -X POST http://localhost:8002/case-intake/route \
  -H "Content-Type: application/json" \
  -d @"$ROOT_DIR/uipath-workflows/http-request-bodies/case-intake-route-po-1004.json"
assert_saved_json "PO-1003 remains XAML workflow proposal candidate" "case-intake-route-po-1003.json" \
  "body['capability_decision'] == 'XAML_WORKFLOW_PROPOSAL'"

save_json "Approval task with agent reasoning" "approval-task-created.json" \
  -X POST http://localhost:8002/approvals/create \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "CASE-EVIDENCE-APPROVAL",
    "po_id": "PO-1001",
    "amount": 18000,
    "budget_limit": 10000,
    "erp_status": "Exception",
    "raw_exception_text": "Amount exceeds approved budget limit",
    "business_remarks": "Q4 customer delivery is at risk. Finance asks whether this should be approved due to strategic account impact.",
    "agent_reasoning_summary": "Agent used finance policy and Q4 renewal pressure before requiring human approval.",
    "company_context_reference": {
      "finance_policy_used": true,
      "sales_context_used": true,
      "operations_context_used": true
    },
    "policy_gate_reason": "Budget exception requires manager approval.",
    "agent_recommendation": "WAITING_FOR_HUMAN_APPROVAL",
    "reason": "No ERP approval click is recommended.",
    "policy_decision": "REQUIRE_HUMAN_APPROVAL",
    "requested_by": "evidence-pack"
  }'
assert_saved_json "Approval task was created" "approval-task-created.json" \
  "body['status'] == 'PENDING' and body['approval_url'].startswith('/approvals/')"

save_json "Proposal inbox" "proposals-inbox.json" http://localhost:8002/proposals/inbox?format=json
assert_saved_json "Proposal inbox has API and XAML proposals" "proposals-inbox.json" \
  "body['total'] >= 2 and any(p['proposal_type'] == 'API_MODERNIZATION_PROPOSAL' for p in body['proposals']) and any(p['proposal_type'] == 'XAML_WORKFLOW_PROPOSAL' for p in body['proposals'])"

save_json "Demo evidence snapshot" "demo-evidence-snapshot.json" http://localhost:8002/demo/evidence-snapshot
assert_saved_json "Evidence snapshot includes safety boundaries" "demo-evidence-snapshot.json" \
  "body['safety_boundaries']['no_auto_xaml_modification'] is True and body['safety_boundaries']['no_auto_api_deployment'] is True"

save_json "Simulation state" "simulation-state.json" http://localhost:8002/simulation/state

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
    "demo_track": "rpa_first_erp_worker_enterprise_context",
    "service_ports": {
        "mock_legacy_erp": 8001,
        "reasoning_agent": 8002,
        "generated_api_facade": 8003,
        "validation_suite": 8004
    },
    "primary_entry_points": [
        "GET http://localhost:8002/erp/work-queue",
        "GET http://localhost:8002/company-context",
        "POST http://localhost:8002/case-intake/route",
        "GET http://localhost:8002/simulation/dashboard",
        "GET http://localhost:8002/proposals/inbox"
    ],
    "canonical_cases": [
        "PO-1000 standard deterministic path",
        "PO-1001 budget exception with enterprise context and web approval",
        "PO-1002 vendor data wait",
        "PO-1003 inventory shortage capability gap / XAML proposal candidate",
        "PO-1004 ambiguous manual investigation"
    ],
    "proposal_threshold": 3,
    "safety_boundaries": [
        "no automatic Codex call before human proposal approval",
        "no automatic API deployment",
        "no automatic trusted capability registration",
        "no automatic Windows XAML modification"
    ],
    "evidence_files": evidence_files
}

with open(out_dir / "manifest.json", "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2)
    handle.write("\n")
PY
echo "[OK] Evidence manifest generated"

echo "Evidence written to $OUT_DIR"
