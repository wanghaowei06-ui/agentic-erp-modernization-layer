# UiPath Implementation Guide

UiPath is the main orchestration layer for this MVP. UiPath creates and governs the modernization case, drives RPA extraction from the legacy ERP UI, invokes support services, routes the case, handles human approval, governs validation and trusted-tool approval, and switches execution from RPA to API when approved.

## Variables

- `case_id`
- `po_id`
- `current_stage`
- `detected_exception_type`
- `risk_level`
- `requires_human_approval`
- `human_approval_status`
- `execution_mode`
- `validation_status`
- `trusted_tool_status`
- `triage_result_json`
- `validation_result_json`
- `api_result_json`

## Endpoints UiPath Should Call

- Legacy ERP UI for RPA scraping and clicking: `http://localhost:8000/purchase-orders`
- Triage support service: `POST http://localhost:8001/triage`
- Validation support service: `POST http://localhost:8003/validate/request-purchase-order-approval`
- API facade candidate after approval: `POST http://localhost:8002/api/purchase-orders/{po_id}/approval-request`
- API facade OpenAPI document: `GET http://localhost:8002/openapi.yaml`

## Suggested UiPath Stages

1. Case Intake
2. Legacy ERP Extraction
3. Exception Triage
4. Dynamic Routing
5. Human Approval
6. Legacy RPA Write-back
7. Validation Gate
8. Trusted Tool Approval
9. API Mode Execution

## RPA Extraction Fields

On `http://localhost:8000/purchase-orders/{po_id}`, UiPath can scrape:

- `po-id`
- `amount`
- `budget-limit`
- `vendor-id`
- `vendor-info-complete`
- `inventory-available`
- `erp-status`
- `raw-exception-text`

For RPA write-back, UiPath should populate and click:

- `approval-reason-input`
- `manager-id-input`
- `request-approval-button`

The confirmation page exposes:

- `writeback-result`
- `writeback-status`
- `writeback-execution-mode`
- `writeback-audit-created`

## Expected Final Case JSON

```json
{
  "case_id": "CASE-001",
  "po_id": "PO-1001",
  "current_stage": "API Mode Execution",
  "detected_exception_type": "budget_exceeded",
  "risk_level": "high",
  "requires_human_approval": true,
  "human_approval_status": "approved",
  "execution_mode": "API",
  "validation_status": "passed",
  "trusted_tool_status": "approved",
  "triage_result_json": {
    "detected_exception_type": "budget_exceeded",
    "recommended_path": "manager_approval_required",
    "next_stage": "WAITING_FOR_HUMAN_APPROVAL"
  },
  "validation_result_json": {
    "business_action": "request_purchase_order_approval",
    "rpa_api_parity_check": "passed",
    "trusted_tool_candidate": true,
    "requires_registration_approval": true
  },
  "api_result_json": {
    "po_id": "PO-1001",
    "status": "PENDING_MANAGER_APPROVAL",
    "audit_log_created": true,
    "execution_mode": "API",
    "source_case_id": "CASE-001"
  }
}
```
