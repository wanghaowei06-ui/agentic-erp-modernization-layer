# Validation Gate

The validation suite is a local support service called by UiPath before a generated API candidate is treated as a trusted tool.

Endpoint:

`POST http://localhost:8004/validate/request-purchase-order-approval`

## Contract Test

`contract_tests.py` checks the generated API facade request and response shape:

- Request fields: `approval_reason`, `manager_id`, `source_case_id`
- Response fields: `po_id`, `status`, `audit_log_created`, `execution_mode`, `source_case_id`
- Required execution mode: `API`
- Required audit behavior: `audit_log_created=true`

## Business Rule Test

`business_rule_tests.py` checks the rule for the Hard MVP action:

- If `amount > budget_limit`, the purchase order cannot be silently approved.
- The required result is `PENDING_MANAGER_APPROVAL`.

## RPA/API Parity Check

`parity_check.py` uses cloned data:

- `PO-1001-RPA`
- `PO-1001-API`

Both records start from the same initial state:

- `amount=18000`
- `budget_limit=10000`
- `vendor_id=V-203`
- `vendor_info_complete=true`
- `inventory_available=true`
- `status=Exception`
- `raw_exception_text=Amount exceeds approved budget limit`

The check does not compare an API result against `PO-1001` after RPA has already changed it. The Hard MVP parity result compares cloned baseline outcomes for `status` and `audit_log_created`.

This is a Hard MVP demo heuristic, not proof that every production WebForms side effect has been captured. For production, the same contract would need live RPA trace capture, richer side-effect assertions, and environment-controlled test data resets.

## Registration Gate

A passed validation response marks the facade as a trusted-tool candidate and sets `requires_registration_approval=true`. It does not automatically approve or deploy the capability. UiPath and the automation owner still govern registration and API-mode execution.
