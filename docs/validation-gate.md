# Validation Gate

The validation suite is a support service called by UiPath before trusted-tool registration and API-mode execution.

## Contract Test

The contract test verifies that the generated API facade accepts the expected request fields and returns the expected response fields for `request_purchase_order_approval`.

## Business Rule Test

The business rule test verifies that an approval request changes the purchase order status to `PENDING_MANAGER_APPROVAL`, records `last_action` as `approval_requested`, and creates an audit log.

## Parity Check

The RPA/API parity check compares cloned and reset test cases:

- `PO-1001-RPA`
- `PO-1001-API`

Both cases start from the same initial state. The validation result compares `status`, `audit_log_created`, and `last_action`.

The API result is not compared against a record already mutated by the RPA path. The cloned test cases keep the parity result isolated and repeatable.

## Registration Approval

Passing validation marks the API facade as a trusted-tool candidate. UiPath still governs registration approval before API-mode execution.
