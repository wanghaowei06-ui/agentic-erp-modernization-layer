#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

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

assert_json "Mock ERP health" "body['status'] == 'ok'" http://localhost:8001/health
assert_json "Reasoning Agent health" "body['status'] == 'ok'" http://localhost:8002/health
assert_json "Generated API health" "body['status'] == 'ok'" http://localhost:8003/health
assert_json "Validation Suite health" "body['status'] == 'ok'" http://localhost:8004/health

assert_json "Enterprise context is available" \
  "body['company']['name'] == 'Demo Manufacturing Group' and body['company']['finance_policy']['requires_manager_approval_above_budget'] is True" \
  http://localhost:8002/company-context

assert_json "PO-1000 stays deterministic" \
  "body['final_route'] == 'STANDARD_PROCESSING' and body['agent_required'] is False and body['precheck_decision_source'] == 'deterministic_rule' and body['llm_validation_proof']['llm_invocation_verified'] is False" \
  -X POST http://localhost:8002/case-intake/route \
  -H "Content-Type: application/json" \
  -d @"$ROOT_DIR/uipath-workflows/http-request-bodies/case-intake-route-po-1000.json"

assert_json "PO-1001 uses agent context and creates approval route" \
  "body['final_route'] == 'WAITING_FOR_HUMAN_APPROVAL' and body['policy_gate']['human_required'] is True and body['agent_context_used'] is True and body['company_context_reference']['finance_policy_used'] is True and body['llm_validation_proof']['llm_invocation_verified'] is True and body['recommended_erp_action']['action_id'] == 'CREATE_WEB_APPROVAL_TASK'" \
  -X POST http://localhost:8002/case-intake/route \
  -H "Content-Type: application/json" \
  -d @"$ROOT_DIR/uipath-workflows/http-request-bodies/case-intake-route-po-1001.json"

assert_json "PO-1002 maps to vendor wait button" \
  "body['final_route'] == 'WAITING_VENDOR_INFO' and body['recommended_erp_action']['button_selector_id'] == 'ctl00_MainContent_btnMarkWaitingVendor'" \
  -X POST http://localhost:8002/case-intake/route \
  -H "Content-Type: application/json" \
  -d @"$ROOT_DIR/uipath-workflows/http-request-bodies/case-intake-route-po-1002.json"

assert_json "PO-1003 maps to XAML capability-gap proposal path" \
  "body['final_route'] == 'CAPABILITY_GAP_DETECTED' and body['capability_decision'] == 'XAML_WORKFLOW_PROPOSAL' and body['recommended_erp_action']['button_selector_id'] == 'ctl00_MainContent_btnFlagCapabilityGap'" \
  -X POST http://localhost:8002/case-intake/route \
  -H "Content-Type: application/json" \
  -d @"$ROOT_DIR/uipath-workflows/http-request-bodies/case-intake-route-po-1003.json"

assert_json "PO-1004 maps to manual investigation" \
  "body['final_route'] == 'WAITING_MANUAL_INVESTIGATION' and body['recommended_erp_action']['button_selector_id'] == 'ctl00_MainContent_btnSendManualInvestigation'" \
  -X POST http://localhost:8002/case-intake/route \
  -H "Content-Type: application/json" \
  -d @"$ROOT_DIR/uipath-workflows/http-request-bodies/case-intake-route-po-1004.json"

assert_json "Approval task carries business remarks and agent reasoning" \
  "body['status'] == 'PENDING' and body['approval_url'].startswith('/approvals/')" \
  -X POST http://localhost:8002/approvals/create \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "CASE-SMOKE-APPROVAL",
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
    "requested_by": "smoke-test"
  }'

assert_json "Proposal inbox exposes threshold-triggered proposals" \
  "body['total'] >= 2 and any(p['proposal_type'] == 'API_MODERNIZATION_PROPOSAL' for p in body['proposals']) and any(p['proposal_type'] == 'XAML_WORKFLOW_PROPOSAL' for p in body['proposals']) and all(p.get('auto_execution_allowed') is False for p in body['proposals'])" \
  http://localhost:8002/proposals/inbox?format=json

assert_json "Demo evidence snapshot keeps safety boundaries" \
  "body['safety_boundaries']['no_auto_xaml_modification'] is True and body['safety_boundaries']['no_auto_api_deployment'] is True and body['simulation_summary']['proposal_threshold'] == 3" \
  http://localhost:8002/demo/evidence-snapshot
