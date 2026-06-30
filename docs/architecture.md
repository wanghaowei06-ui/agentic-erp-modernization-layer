# Architecture

## Runtime View

```text
UiPath RPA Worker (Main.xaml)
  -> reasoning-agent on 8002
       - ERP work queue/detail UI
       - /case-intake/route LangGraph agent path
       - /company-context mock enterprise context
       - approvals, dashboards, proposals, Codex handoff UI
       - Run Memory / Pattern Memory APIs
  -> Mock Legacy ERP support service on 8001
  -> generated-api-facade on 8003
  -> validation-suite on 8004
  -> shared Automation Memory / structured evidence files
```

## UiPath Layer

UiPath remains the execution and governance layer.

Responsibilities:

- open the ERP work queue at `http://localhost:8002/erp/work-queue`
- extract purchase-order fields and business remarks from stable selectors
- call the route agent over HTTP
- branch by `final_route` and `policy_decision`
- create human approval tasks when required
- click only the recommended safe ERP action for non-approval routes
- write Run Memory evidence and commit Pattern Memory
- keep proposals and Codex handoff human-approved

## Reasoning Agent

The reasoning-agent is the main local demo service.

Current behavior:

- exposes the ERP work queue/detail pages used by UiPath
- exposes `GET /company-context`
- exposes `POST /case-intake/route`
- uses deterministic precheck for normal cases
- uses LangGraph-backed coded agent routing for agent-required cases
- combines ERP fields, system exception text, business remarks, and mock
  enterprise context
- returns `final_route`, `policy_gate`, `agent_reasoning_summary`,
  `llm_validation_proof`, and `recommended_erp_action`
- records and visualizes Run Memory and Pattern Memory
- generates modernization proposals only from accumulated pattern evidence
- starts Codex handoff only after human proposal approval

Important endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/erp/work-queue` | UiPath-facing ERP work queue. |
| `GET` | `/erp/work-queue/{id}` | ERP detail page with stable selectors. |
| `GET` | `/company-context` | Mock enterprise context. |
| `POST` | `/case-intake/route` | Current UiPath route agent endpoint. |
| `POST` | `/approvals/create` | Create human approval task. |
| `GET` | `/approvals/inbox` | Human approval inbox. |
| `GET` | `/simulation/dashboard` | Pattern Memory dashboard. |
| `GET` | `/proposals/inbox` | Threshold-triggered proposal inbox. |
| `GET` | `/demo/agent-context-trace` | Read-only agent context trace. |

`POST /triage` remains available for compatibility tests and older route proof
workflows. It is not the current main UiPath demo entry point.

## LangGraph Agent Runtime

The coded agent service uses LangGraph:

- `StateGraph` models the guarded decision path.
- `MemorySaver` stores case-level agent checkpoints.
- Structured Run Memory under `memory/runs/`, `memory/patterns/`, and
  `memory/proposals/` remains the audit system of record.

## Mock Legacy ERP

Mock Legacy ERP on port `8001` remains as a support service for legacy
purchase-order behavior and API/RPA parity examples. The current business-facing
ERP Worker demo page is served by reasoning-agent on port `8002`.

## Validation Suite And API Facade

The validation suite and generated API facade are retained for the modernization
story:

- validation suite: `POST /validate/request-purchase-order-approval`
- API facade: `POST /api/purchase-orders/{po_id}/approval-request`

They demonstrate how a repeated RPA pattern can be validated before a future
API modernization proposal becomes trusted. They do not auto-deploy or
auto-register capabilities.

## Memory And Proposals

Run Memory records:

- ERP extracted fields
- business remarks
- company context snapshot/reference
- route plan
- agent reasoning summary
- LLM validation proof
- policy gate
- selected ERP action
- final branch result

Pattern Memory groups by:

```text
business_action + exception_type + route_family + policy_gate_family + side_effects_signature
```

When observed count reaches the configured threshold, the system creates a
proposal:

- `API_MODERNIZATION_PROPOSAL`
- `XAML_WORKFLOW_PROPOSAL`

All proposals are review-only until a human approves them.

## Safety Boundary

The system does not automatically approve, deploy APIs, register trusted
capabilities, or modify Windows XAML. UiPath selectors are preserved, and
modernization/XAML/API changes remain proposal or human-approved handoff steps.
