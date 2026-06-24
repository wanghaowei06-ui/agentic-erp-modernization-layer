# Architecture

## Runtime View

```text
UiPath Orchestration Layer
  -> Mock Legacy ERP on 8001
  -> reasoning-agent on 8002
  -> generated-api-facade on 8003
  -> validation-suite on 8004
  -> shared/automation_memory JSON/JSONL adapter
```

## UiPath Layer

UiPath is the orchestration, human approval, and execution governance layer.

Responsibilities:

- open and read the legacy ERP UI
- call reasoning-agent by HTTP
- route by `detected_exception_type`
- keep Human Approval in control
- perform RPA write-back
- call validation-suite
- call generated-api-facade after validation
- produce the final case output

## Mock Legacy ERP

Mock Legacy ERP simulates a legacy enterprise ERP / WebForms-style UI.

Current role:

- exposes PO pages for UiPath RPA
- accepts RPA write-back through `POST /purchase-orders/{po_id}/request-approval`
- writes `RPA_WRITEBACK_COMPLETED` to Automation Memory after successful write-back

## reasoning-agent

The Hard MVP reasoning-agent is a structured decision service.

Current behavior:

- default decision source: `deterministic_rule`
- endpoint: `POST /triage`
- emits decision object only
- writes `TRIAGE_COMPLETED`
- does not execute business actions

Future options:

- `llm_structured`
- `hybrid_guarded`
- schema validation
- deterministic guardrails
- fail-closed fallback

## validation-suite

The validation-suite validates whether a candidate API path can be trusted for the demo action.

Current behavior:

- contract test
- business rule test
- cloned-data RPA/API parity heuristic
- writes `VALIDATION_COMPLETED`
- registers trusted capabilities after passed validation
- exposes read-only memory query APIs

## generated-api-facade

The generated API facade simulates the validated API-mode execution path.

Current behavior:

- endpoint: `POST /api/purchase-orders/{po_id}/approval-request`
- returns `execution_mode=API`
- writes `API_EXECUTION_COMPLETED`

## Automation Memory Layer

Automation Memory is the governed system of record.

It stores:

- case events
- agent decisions
- RPA execution traces
- validation results
- API execution traces
- trusted capabilities
- capability gaps

Current implementation:

- module: `shared/automation_memory`
- adapter: JSON/JSONL
- event stream: `memory-data/events.jsonl`
- capability registry: `memory-data/capabilities.json`
- repository-first API for future SQLite/PostgreSQL migration

Vector memory or graph memory can be future auxiliary retrieval layers. They are not the audit source in this MVP.

## Event Types

Current implemented runtime events:

- `TRIAGE_COMPLETED`
- `RPA_WRITEBACK_COMPLETED`
- `VALIDATION_COMPLETED`
- `API_EXECUTION_COMPLETED`
- `CAPABILITY_GAP_RECORDED`
- `CAPABILITY_REGISTERED`

## Boundary

The system does not automatically generate, approve, publish, or execute new XAML. Unsupported work is recorded as a capability gap for governed follow-up.
