# Draft PR Handoff Summary

## Candidate

- Business action: `manual_investigation`
- Process signature: `manual_investigation__budget_exceeded__waiting_for_human_approval__require_human_approval__no_side_effects`
- Proposed endpoint: `POST /api/purchase-orders/{po_id}/approval-request`
- Evidence runs: `RUN-20260630-001`, `RUN-20260630-002`, `RUN-20260630-003`, `RUN-20260630-004`
- Observed count used for this handoff: `4`

## Local Code Change

- Updates the generated FastAPI facade to return `202 Accepted` with a stable `task_id`.
- Adds an `approval_tasks` SQLite table and creates/reuses one pending task per PO.
- Writes an audit row with `action=human_approval_task_created` and `execution_mode=HUMAN_APPROVAL`.
- Preserves the no-side-effect signature by leaving purchase order status unchanged.
- Updates the checked-in OpenAPI YAML to describe the `202` approval-task contract.

## Review Notes

- This is code for human review only.
- This change does not deploy, register, or mark the endpoint as trusted.
- Validation and trusted-tool registration remain separate governance steps.
- The endpoint creates a pending human approval task; it does not approve the PO or perform ERP writeback.

## Suggested PR Checks

- Run `python -m pytest generated-api-facade`.
- Confirm `POST /api/purchase-orders/{po_id}/approval-request` returns `202`.
- Confirm the response contains `task_id`.
- Confirm `audit_logs` receives one task-created audit row.
- Confirm `purchase_orders.status` remains unchanged for the no-side-effect path.
