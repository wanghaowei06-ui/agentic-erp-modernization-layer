# Main Workflow Outline

This is a precise UiPath workflow outline. It is not Python orchestration. UiPath must remain the main orchestration and governance layer.

## Sequence: Initialize Case

1. Assign `case_id = "CASE-001"`.
2. Assign `po_id = "PO-1001"`.
3. Assign `current_stage = "CASE_INTAKE"`.
4. Assign `execution_mode = "RPA"`.
5. Assign `human_approval_status = "pending"`.
6. Assign `validation_status = "not_started"`.
7. Assign `trusted_tool_status = "not_registered"`.
8. Log message: `Case Intake started for CASE-001 / PO-1001`.

## Sequence: Legacy ERP Extraction

1. Use Browser or Open Browser: `http://localhost:8000/purchase-orders/PO-1001`.
2. Get Text from stable ID `po-id`; assign to `po_id`.
3. Get Text from stable ID `amount`; convert to number and assign to `amount`.
4. Get Text from stable ID `budget-limit`; convert to number and assign to `budget_limit`.
5. Get Text from stable ID `vendor-id`; assign to `vendor_id`.
6. Get Text from stable ID `vendor-info-complete`; convert to Boolean and assign to `vendor_info_complete`.
7. Get Text from stable ID `inventory-available`; convert to Boolean and assign to `inventory_available`.
8. Get Text from stable ID `erp-status`; assign to `erp_status`.
9. Get Text from stable ID `raw-exception-text`; assign to `raw_exception_text`.
10. Assign `current_stage = "LEGACY_ERP_EXTRACTION_COMPLETE"`.

## Sequence: Exception Triage

1. Build request body from the extracted values.
2. HTTP Request:
   - Method: `POST`
   - URL: `http://localhost:8001/triage`
   - Body: JSON matching `http-request-bodies/triage-po-1001.json`
3. Assign response body to `triage_result_json`.
4. Deserialize JSON.
5. Assign parsed values:
   - `detected_exception_type`
   - `risk_level`
   - `requires_human_approval`
   - `next_stage`
6. Assign `current_stage = "EXCEPTION_TRIAGE_COMPLETE"`.

## Flow Switch: Dynamic Routing

Use a Flow Switch or equivalent conditional activity on `detected_exception_type`.

The router must use `detected_exception_type`, not `po_id`.

### Branch: `budget_exceeded`

1. Assign `current_stage = "WAITING_FOR_HUMAN_APPROVAL"`.
2. Create or display a business human approval task.
3. If approved, assign `human_approval_status = "approved"`.
4. Continue to Legacy RPA Write-back.

### Branch: `vendor_info_missing`

1. Assign `current_stage = "WAITING_VENDOR_INFO"`.
2. Log or show the lightweight PO-1002 route proof.
3. Do not use PO ID as the routing condition.
4. Continue only if the demo script intentionally returns to PO-1001.

### Branch: `inventory_shortage`

1. Assign `current_stage = "WAITING_INVENTORY_REVIEW"`.
2. Create or display an inventory review task placeholder.

### Branch: `unknown_exception`

1. Assign `current_stage = "WAITING_MANUAL_INVESTIGATION"`.
2. Create or display a manual investigation task placeholder.

## Sequence: Legacy RPA Write-back

1. Use the existing browser session on PO-1001 detail page.
2. Type into `approval-reason-input`: `Amount exceeds budget limit`.
3. Type into `manager-id-input`: `MGR-001`.
4. Click `request-approval-button`.
5. Get Text from `writeback-status`; verify `PENDING_MANAGER_APPROVAL`.
6. Get Text from `writeback-execution-mode`; verify `RPA`.
7. Get Text from `writeback-audit-created`; verify `true`.
8. Assign `current_stage = "LEGACY_RPA_WRITEBACK_COMPLETE"`.

## Sequence: Validation Gate

1. HTTP Request:
   - Method: `POST`
   - URL: `http://localhost:8003/validate/request-purchase-order-approval`
   - Body: `{}` or an empty JSON object.
2. Assign response body to `validation_result_json`.
3. Deserialize JSON.
4. Verify:
   - `contract_test = "passed"`
   - `business_rule_test = "passed"`
   - `rpa_api_parity_check = "passed"`
   - `trusted_tool_candidate = true`
5. Assign `validation_status = "passed"`.
6. Assign `current_stage = "VALIDATION_GATE_PASSED"`.

## Sequence: Trusted Tool Approval

1. Create or display trusted tool registration approval.
2. If approved, assign `trusted_tool_status = "registered"`.
3. Assign `current_stage = "TRUSTED_TOOL_REGISTERED"`.

## Sequence: API Mode Execution

1. HTTP Request:
   - Method: `POST`
   - URL: `http://localhost:8002/api/purchase-orders/PO-1001/approval-request`
   - Body: JSON matching `http-request-bodies/api-mode-request.json`
2. Assign response body to `api_result_json`.
3. Deserialize JSON.
4. Verify `execution_mode = "API"`.
5. Assign `execution_mode = "API"`.
6. Assign `current_stage = "API_MODE_EXECUTED"`.

## Sequence: Build Final Case JSON

1. Build `final_case_output_json` with:
   - `case_id`
   - `po_id`
   - `current_stage`
   - `detected_exception_type`
   - `risk_level`
   - `human_approval_status`
   - `validation_status`
   - `trusted_tool_status`
   - `execution_mode`
2. Log final output.
3. Display or save final output for the demo evidence.
