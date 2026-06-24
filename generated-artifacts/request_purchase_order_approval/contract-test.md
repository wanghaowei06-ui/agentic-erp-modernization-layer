# Contract Test Requirements

These are requirements for validation-suite execution. This artifact does not claim tests have run.

- Request must include `approval_reason`.
- Request must include `manager_id`.
- Request must include `source_case_id`.
- Response must include `po_id`.
- Response must include `status`.
- Response must include `audit_log_created`.
- Response must include `execution_mode`.
- Response must include `source_case_id`.
- Response must include `side_effects`.
- Response must include `event_trace_id`.
- `side_effects` must include `PO_STATUS_UPDATED`.
- `side_effects` must include `APPROVAL_TASK_CREATED`.
- `side_effects` must include `AUDIT_LOG_CREATED`.
- `side_effects` must include `MANAGER_NOTIFICATION_QUEUED`.
- `side_effects` must include `BUDGET_REVIEW_FLAGGED`.
