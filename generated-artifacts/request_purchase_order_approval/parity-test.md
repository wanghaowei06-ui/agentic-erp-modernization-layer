# RPA/API Parity Test Requirements

These are requirements for validation-suite execution. This artifact does not claim tests have run.

Compare cloned/reset cases:

- `PO-1001-RPA`
- `PO-1001-API`

Compare fields:

- `status`
- `audit_log_created`
- `last_action`
- `side_effects`

The API result must not be compared against a record already mutated by the RPA path.

Required side effects signature:

- `PO_STATUS_UPDATED`
- `APPROVAL_TASK_CREATED`
- `AUDIT_LOG_CREATED`
- `MANAGER_NOTIFICATION_QUEUED`
- `BUDGET_REVIEW_FLAGGED`
