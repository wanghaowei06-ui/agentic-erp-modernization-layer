# Validation Gate

The validation suite is a local support service for modernization candidates.
It remains available on port `8004`, but it is no longer the first thing to show
in the current recording. The main demo path is UiPath ERP extraction →
`/case-intake/route` → Run Memory → Pattern Memory → proposal threshold.

Endpoint:

```text
POST http://localhost:8004/validate/request-purchase-order-approval
```

## Contract Test

`contract_tests.py` checks the generated API facade request and response shape:

- Request fields: `approval_reason`, `manager_id`, `source_case_id`
- Response fields: `po_id`, `status`, `audit_log_created`, `execution_mode`, `source_case_id`
- Required execution mode: `API`
- Required audit behavior: `audit_log_created=true`

## Business Rule Test

`business_rule_tests.py` checks the budget exception action:

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

The check does not compare an API result against `PO-1001` after RPA has already
changed it. It compares cloned baseline outcomes for `status` and
`audit_log_created`.

## Governance Boundary

A passed validation response can support a proposal or trusted-capability
review, but it does not automatically deploy an API, register a trusted
capability, approve a proposal, or switch UiPath to API execution.
