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

curl_json "mock-legacy-erp health" http://localhost:8000/health
curl_json "reasoning-agent health" http://localhost:8001/health
curl_json "generated-api-facade health" http://localhost:8002/health
curl_json "validation-suite health" http://localhost:8003/health

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

curl_json "validation gate" \
  -X POST http://localhost:8003/validate/request-purchase-order-approval

curl_json "API facade approval request" \
  -X POST http://localhost:8002/api/purchase-orders/PO-1001/approval-request \
  -H "Content-Type: application/json" \
  -d '{
    "approval_reason": "Amount exceeds budget limit",
    "manager_id": "MGR-001",
    "source_case_id": "CASE-001"
  }'
