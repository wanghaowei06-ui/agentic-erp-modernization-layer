#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

curl_json() {
  local response
  echo
  echo "$1"
  shift
  response="$(curl --retry 10 --retry-connrefused --retry-delay 1 -sS "$@")"
  printf '%s\n' "$response" | "$PYTHON_BIN" -m json.tool
}

curl_status() {
  local status
  echo
  echo "$1"
  shift
  status="$(curl --retry 10 --retry-connrefused --retry-delay 1 -sS -o /dev/null -w "%{http_code}" "$@")"
  echo "HTTP $status"
  test "$status" = "200"
}

curl_json "mock-legacy-erp health" http://localhost:8000/health
curl_json "reasoning-agent health" http://localhost:8001/health
curl_json "generated-api-facade health" http://localhost:8002/health
curl_json "validation-suite health" http://localhost:8003/health
curl_status "case dashboard route" http://localhost:8000/case-dashboard
curl_status "case timeline route" http://localhost:8000/case-timeline/CASE-001
curl_status "api readiness scorecard route" http://localhost:8000/api-readiness-scorecard
curl_status "tool registry route" http://localhost:8000/tool-registry
curl_json "case timeline json" http://localhost:8000/api/demo/cases/CASE-001/timeline
curl_json "api readiness scorecard json" http://localhost:8000/api/demo/api-readiness-scorecard
curl_json "tool registry json" http://localhost:8000/api/demo/tool-registry

curl_json "triage PO-1001" \
  -X POST http://localhost:8001/triage \
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

curl_json "triage PO-1002" \
  -X POST http://localhost:8001/triage \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "CASE-002",
    "po_id": "PO-1002",
    "amount": 6000,
    "budget_limit": 10000,
    "vendor_id": null,
    "vendor_info_complete": false,
    "inventory_available": true,
    "erp_status": "Exception",
    "raw_exception_text": "Vendor information missing"
  }'

curl_json "triage PO-1003" \
  -X POST http://localhost:8001/triage \
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

curl_json "validation gate" \
  -X POST http://localhost:8003/validate/request-purchase-order-approval

curl_json "validation failed simulation" \
  -X POST http://localhost:8003/validate/request-purchase-order-approval \
  -H "Content-Type: application/json" \
  -d '{"simulate_failure": true}'

curl_json "API facade approval request" \
  -X POST http://localhost:8002/api/purchase-orders/PO-1001/approval-request \
  -H "Content-Type: application/json" \
  -d '{
    "approval_reason": "Amount exceeds budget limit",
    "manager_id": "MGR-001",
    "source_case_id": "CASE-001"
  }'

curl_json "mock ERP local demo reset" \
  -X POST http://localhost:8000/api/demo/reset
