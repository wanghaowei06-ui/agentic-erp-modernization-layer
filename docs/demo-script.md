# Demo Script

This script is written for a judge or reviewer. It describes the actual Hard MVP runtime path.

## 1. Start Services

Run:

```bash
./scripts/dev-start.sh
```

Confirm:

- `http://localhost:8001/health`
- `http://localhost:8002/health`
- `http://localhost:8003/health`
- `http://localhost:8004/health`

## 2. Open Mock Legacy ERP

Open:

`http://localhost:8001`

UiPath opens the legacy ERP page and reads PO-1001 fields from the UI.

## 3. UiPath Reads PO-1001

PO-1001 starts as an exception:

- amount `18000`
- budget limit `10000`
- vendor `V-203`
- status `Exception`
- raw exception text `Amount exceeds approved budget limit`

## 4. UiPath Calls Triage

UiPath calls:

`POST http://localhost:8002/triage`

The reasoning-agent returns a deterministic structured decision:

- `detected_exception_type=budget_exceeded`
- `next_stage=WAITING_FOR_HUMAN_APPROVAL`
- `business_action=request_purchase_order_approval`
- `decision_source=deterministic_rule`

The agent writes `TRIAGE_COMPLETED` to Automation Memory.

## 5. Human Approval

UiPath routes to Human Approval because this is a high-risk budget exception. The robot and agent do not approve the business action by themselves.

## 6. RPA Write-Back

UiPath performs RPA write-back through Mock ERP:

`POST http://localhost:8001/purchase-orders/PO-1001/request-approval`

Mock ERP updates the PO status and writes `RPA_WRITEBACK_COMPLETED` to Automation Memory.

## 7. Validation

UiPath calls:

`POST http://localhost:8004/validate/request-purchase-order-approval`

The validation-suite checks:

- contract test
- business rule test
- RPA/API parity heuristic using cloned data

It writes `VALIDATION_COMPLETED` and registers trusted capabilities after passed validation.

## 8. API Mode Execution

UiPath calls:

`POST http://localhost:8003/api/purchase-orders/PO-1001-API/approval-request`

The generated API returns `execution_mode=API` and writes `API_EXECUTION_COMPLETED`.

## 9. Automation Memory Timeline

Open:

`http://localhost:8004/memory/cases/CASE-001/timeline`

Show the judge these events:

- `TRIAGE_COMPLETED`
- `RPA_WRITEBACK_COMPLETED`
- `VALIDATION_COMPLETED`
- `API_EXECUTION_COMPLETED`
- `CAPABILITY_REGISTERED`

## 10. Agent Decisions

Open:

`http://localhost:8004/memory/decisions/CASE-001`

This shows the structured triage decision recorded as governed memory.

## 11. Capability Registry

Open:

`http://localhost:8004/memory/capabilities`

Show trusted capability records such as:

- `cap_api_request_po_approval_v1`
- `cap_human_po_approval_v1`

## 12. Capability Gap

PO-1003 demonstrates a missing inventory workflow. UiPath calls triage for PO-1003 and receives `inventory_shortage`.

Open:

`http://localhost:8004/memory/gaps`

Show `CAPABILITY_GAP_RECORDED`. This proves unsupported flows become governed proposals, not uncontrolled runtime automation.
