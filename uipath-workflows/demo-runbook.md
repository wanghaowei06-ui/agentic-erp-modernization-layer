# Demo Runbook

## 1. Start WSL2 Services

From WSL2 Ubuntu:

```bash
cd /home/changv/projects/Uipath
source .venv/bin/activate
./scripts/start_all.sh
```

Confirm the printed Windows/UiPath URLs:

- `http://localhost:8000`
- `http://localhost:8001`
- `http://localhost:8002`
- `http://localhost:8003`

## 2. Run Smoke Test

```bash
./scripts/smoke_test.sh
```

Confirm:

- PO-1001 triage returns `budget_exceeded`.
- PO-1002 triage returns `vendor_info_missing`.
- Validation returns `passed`.
- API mode response returns `execution_mode` as `API`.

## 3. Open Windows Chrome

Open:

```text
http://localhost:8000/purchase-orders
```

Confirm the Mock ERP is visible and PO-1001 and PO-1002 appear in the exception queue.

## 4. Run UiPath Workflow

Run the UiPath workflow from Studio or attended robot. UiPath should drive the case from intake through extraction, triage, routing, approval, write-back, validation, trusted-tool approval, and API mode.

## 5. Show PO-1001 Path

Show UiPath scraping PO-1001 fields from:

```text
http://localhost:8000/purchase-orders/PO-1001
```

Show the triage result:

- `detected_exception_type = budget_exceeded`
- `risk_level = high`
- `requires_human_approval = true`

## 6. Show PO-1002 Route Proof

Use `http-request-bodies/triage-po-1002.json` or a short UiPath branch demo to show:

- `detected_exception_type = vendor_info_missing`
- `risk_level = medium`
- `requires_human_approval = false`

Explain that UiPath routes by `detected_exception_type`, not by `po_id`.

## 7. Show Validation Passed

Call:

```text
POST http://localhost:8003/validate/request-purchase-order-approval
```

Show:

- `contract_test = passed`
- `business_rule_test = passed`
- `rpa_api_parity_check = passed`
- `data_isolation = cloned_test_cases`
- `rpa_test_case_id = PO-1001-RPA`
- `api_test_case_id = PO-1001-API`

## 8. Show API Mode Execution

Call:

```text
POST http://localhost:8002/api/purchase-orders/PO-1001/approval-request
```

Show:

- `status = PENDING_MANAGER_APPROVAL`
- `audit_log_created = true`
- `execution_mode = API`

## 9. Show Final Case Output

Show `expected-outputs/final-case-output.json` or the UiPath log output:

```json
{
  "case_id": "CASE-001",
  "po_id": "PO-1001",
  "current_stage": "API_MODE_EXECUTED",
  "detected_exception_type": "budget_exceeded",
  "risk_level": "high",
  "human_approval_status": "approved",
  "validation_status": "passed",
  "trusted_tool_status": "registered",
  "execution_mode": "API"
}
```
