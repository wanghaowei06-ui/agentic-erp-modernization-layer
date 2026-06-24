# Video Script

Target length: 2 to 3 minutes.

## 1. Problem, 20 Seconds

Legacy ERP processes are hard to modernize safely. The business rules and side effects are often hidden in old UI workflows, approval steps, and exception screens.

Directly replacing RPA with an API can be risky if we do not know whether the API reproduces the same business outcome.

## 2. Solution, 30 Seconds

This project shows a UiPath-governed path from RPA-observed workflow to validated API capability.

UiPath orchestrates RPA, human approval, validation, and trusted API execution. The agent layer provides stable structured decisions. Automation Memory records the important decisions, validations, executions, capabilities, and gaps as governed evidence.

The Hard MVP uses deterministic structured triage for stable governance. LLM structured triage is a future Enhanced Mode option, not a requirement for this demo.

## 3. Demo, 70-100 Seconds

Open Mock Legacy ERP at:

```text
http://localhost:8001
```

Show PO-1001 with a budget exception.

Run the UiPath Hard MVP flow. UiPath reads the legacy ERP fields and calls:

```text
POST http://localhost:8002/triage
```

Show the triage result:

- `detected_exception_type=budget_exceeded`
- `requires_human_approval=true`
- `next_stage=WAITING_FOR_HUMAN_APPROVAL`

Explain that the triage service also writes `TRIAGE_COMPLETED` to Automation Memory.

Show the human approval and RPA write-back path. The legacy ERP writes `RPA_WRITEBACK_COMPLETED`.

Call validation:

```text
POST http://localhost:8004/validate/request-purchase-order-approval
```

Show:

- `contract_test=passed`
- `business_rule_test=passed`
- `rpa_api_parity_check=passed`

Call the generated API facade:

```text
POST http://localhost:8003/api/purchase-orders/PO-1001-API/approval-request
```

Show `execution_mode=API`.

Open the Automation Memory timeline:

```text
http://localhost:8004/memory/cases/CASE-001/timeline
```

Show:

- `TRIAGE_COMPLETED`
- `RPA_WRITEBACK_COMPLETED`
- `VALIDATION_COMPLETED`
- `API_EXECUTION_COMPLETED`

Open capabilities:

```text
http://localhost:8004/memory/capabilities
```

Show trusted API and human task capabilities.

Open gaps:

```text
http://localhost:8004/memory/gaps
```

Show CASE-003 / PO-1003 inventory shortage as a capability gap instead of uncontrolled automation.

## 4. Why It Matters, 20 Seconds

The system creates a governed migration path from fragile RPA to API modernization. Humans stay in control, and every important automation decision and execution step is auditable.

## 5. Roadmap, 10 Seconds

Enhanced Mode can add LLM structured triage with schema validation, guardrails, and fail-closed fallback. The Hard MVP baseline stays stable even without an LLM API key.
