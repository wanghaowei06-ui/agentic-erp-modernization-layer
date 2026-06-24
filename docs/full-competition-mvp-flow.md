# Full Competition MVP Flow

This document describes the complete demo loop for the current Hard MVP plus optional enhanced evidence surfaces. It does not move orchestration into Python. UiPath remains responsible for case lifecycle, RPA, human approval, validation governance, trusted capability approval, and API-mode execution.

The current Hard MVP uses deterministic structured triage for stable governance. LLM structured triage, modernization readiness scoring, and modernization plan generation are Enhanced Mode concepts and are not required for the main smoke test or UiPath path.

## Flow

```text
Dynamic Case Selection
-> RPA Extract
-> Deterministic Structured Agent Triage
-> Human Approval
-> RPA Write-back
-> Validation Gate
-> Automation Memory Timeline
-> Trusted Capability Evidence
-> API Mode Execution
```

Optional Enhanced Mode evidence can add readiness scoring, modernization planning, and draft PR handoff after the Hard MVP path is already working.

## Hard MVP Support Endpoints

- `GET http://localhost:8001/health`
- `POST http://localhost:8002/triage`
- `POST http://localhost:8001/purchase-orders/{po_id}/request-approval`
- `POST http://localhost:8004/validate/request-purchase-order-approval`
- `POST http://localhost:8003/api/purchase-orders/{po_id}/approval-request`
- `GET http://localhost:8004/memory/cases/CASE-001/timeline`
- `GET http://localhost:8004/memory/capabilities`
- `GET http://localhost:8004/memory/gaps`

## Optional Enhanced Evidence Endpoints

- `GET http://localhost:8001/api/demo/cases`
- `GET http://localhost:8001/api/demo/cases/next?strategy=modernization_value`
- `POST http://localhost:8002/modernization/readiness` if an enhanced readiness endpoint is enabled
- `POST http://localhost:8002/modernization/plan` if an enhanced planning endpoint is enabled
- `POST http://localhost:8001/api/demo/modernization/tasks`
- `POST http://localhost:8001/api/demo/tool-registry/register`

## Agent Runtime

The current reasoning service exposes a structured decision endpoint. In Hard MVP mode it uses deterministic rules and returns a stable schema for UiPath JSON Deserialize activities. Python still does not own the business case lifecycle; UiPath calls the endpoint and governs routing, approval, validation, registration, and API mode.

Hard MVP triage:

- `amount > budget_limit` -> `budget_exceeded`
- missing vendor information -> `vendor_info_missing`
- inventory unavailable -> `inventory_shortage`
- otherwise -> `unknown_exception`

The triage service writes `TRIAGE_COMPLETED` to Automation Memory. The response still exposes the original Hard MVP fields such as `detected_exception_type`, `requires_human_approval`, `next_stage`, and `evidence`.

Enhanced Mode can add an LLM structured triage path later, with schema validation, guardrails, and fail-closed fallback. It should not replace UiPath governance or become a required dependency for the Hard MVP demo.

Enhanced readiness and planning graphs, when enabled, should be treated as support evidence only:

Readiness graph:

- `START`
- `collect_evidence`
- `llm_readiness`
- `validate_schema`
- `apply_guardrails`
- `END`

Plan graph:

- `START`
- `build_plan_context`
- `llm_generate_plan`
- `validate_plan_schema`
- `END`

If an Enhanced Mode LLM path is enabled, missing model credentials, model errors, or invalid model JSON should fail closed for that enhanced feature and fall back to the governed Hard MVP path where appropriate.

Optional local LLM configuration for Enhanced Mode:

```bash
cp .env.example .env
# Edit .env and set:
# LLM_API_KEY=your-model-api-key
./scripts/start_all.sh
```

The Hard MVP does not require an LLM API key.

## Legacy Button Side Effects Parity

The `request_purchase_order_approval` action uses this side effects signature:

- `PO_STATUS_UPDATED`
- `APPROVAL_TASK_CREATED`
- `AUDIT_LOG_CREATED`
- `MANAGER_NOTIFICATION_QUEUED`
- `BUDGET_REVIEW_FLAGGED`

UiPath RPA observes the legacy UI button behavior first. The generated API facade is considered safe for API mode only when validation confirms the API reproduces the same business side effects.

Demo trace evidence:

```text
GET http://localhost:8004/memory/cases/CASE-001/timeline
```

API mode response includes `execution_mode=API`, and the generated API facade writes `API_EXECUTION_COMPLETED` to Automation Memory.

## Codex Draft PR Handoff

The modernization task endpoint and artifacts under `generated-artifacts/request_purchase_order_approval/` are optional Enhanced Mode evidence. They describe a possible plan, API candidate, and test requirements.

The script `scripts/create_codex_draft_pr.sh` is optional. It creates a draft PR only when `CREATE_DRAFT_PR=true`. It never auto-merges.
