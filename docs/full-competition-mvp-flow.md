# Full Competition MVP Flow

This is the current competition demo flow. UiPath remains the RPA and execution
governance layer; the Python services provide ERP pages, coded-agent routing,
memory, approvals, proposals, and evidence views.

## Flow

```text
UiPath opens ERP Work Queue
-> UiPath extracts PO fields and business remarks
-> UiPath starts Run Memory
-> UiPath calls /case-intake/route
-> Agent reads mock enterprise context when required
-> Agent returns route, policy gate, reasoning, LLM proof, ERP action
-> UiPath branches safely
-> Human approval task is created when required
-> Run Memory is committed
-> Pattern Memory accumulates evidence
-> Threshold creates API/XAML proposal
-> Human approves proposal
-> Codex handoff timeline starts
```

## Current Support Endpoints

- `GET http://localhost:8002/erp/work-queue`
- `GET http://localhost:8002/company-context`
- `POST http://localhost:8002/case-intake/route`
- `POST http://localhost:8002/approvals/create`
- `GET http://localhost:8002/approvals/inbox`
- `POST http://localhost:8002/memory/runs/start`
- `POST http://localhost:8002/memory/runs/{run_id}/complete`
- `POST http://localhost:8002/memory/runs/{run_id}/commit`
- `GET http://localhost:8002/simulation/dashboard`
- `GET http://localhost:8002/proposals/inbox`

## Agent Runtime

The current route agent is a coded Python/LangGraph service. It uses:

- deterministic precheck for normal cases
- mock or real LLM-backed route reasoning for agent-required cases
- schema validation and guardrails
- mock enterprise context from `/company-context`
- explicit proof fields such as `agent_context_used`,
  `company_context_reference`, and `llm_validation_proof`

Normal deterministic cases are not presented as LLM decisions.

## Proposal Trigger

Proposals are generated from Pattern Memory, not from a UI button. The default
threshold is `3` committed observations. Proposal families:

- `API_MODERNIZATION_PROPOSAL`
- `XAML_WORKFLOW_PROPOSAL`

All proposals require human review before Codex handoff or any implementation
work.

## Legacy Compatibility

Older support endpoints such as `POST /triage` and the mock ERP PO pages on port
`8001` remain available for tests and historical evidence. They are not the
current primary UiPath demo path.
