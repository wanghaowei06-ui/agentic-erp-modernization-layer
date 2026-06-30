# Architecture

## Runtime View

```text
UiPath Orchestration Layer
  -> Mock Legacy ERP on 8001
  -> reasoning-agent on 8002
  -> generated-api-facade on 8003
  -> validation-suite on 8004
  -> shared/automation_memory SQLite + JSON capability registry
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
- queries the capability registry before returning (`find_capability`) and
  populates `memory_references` so callers can see whether a trusted capability
  already covers the business action (PRD 22.1)
- does not execute business actions

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/triage` | Classify an ERP exception and recommend next stage |
| `POST` | `/modernization/readiness` | Assess modernization readiness for a business action |
| `POST` | `/modernization/plan` | Generate a modernization plan (Coding Agent input) |
| `POST` | `/capability-evolution/evaluate` | Evaluate the Capability Evolution Loop trigger (PRD 18.2) |

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
- records human approvals via `POST /approvals` (PRD 11.4)
- exposes read-only memory query APIs

Endpoints:

| Method | Path | Purpose | Auth |
|---|---|---|---|
| `POST` | `/validate/request-purchase-order-approval` | Run validation gate | API key (PRD 17.6) |
| `POST` | `/approvals` | Record a human approval decision | API key (PRD 17.6) |
| `POST` | `/capability-gaps/inventory-shortage` | Record a capability gap | API key (PRD 17.6) |
| `GET` | `/memory/...` | Read-only memory queries | public |

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
- human approvals

### Storage backend

Current implementation:

- module: `shared/automation_memory`
- event store: SQLite at `memory-data/events.db` (replaces the former
  `events.jsonl` full-scan file). Events are indexed on `case_id`,
  `event_type` and `created_at` so timeline / decision / gap queries no longer
  scan the whole table.
- capability registry: `memory-data/capabilities.json` (small list, JSON is
  sufficient)
- repository-first API for future PostgreSQL migration

The JSONL format is retained only as an **export** for audit and backup via
`export_events_jsonl()`. A one-time migration script
(`scripts/migrate_jsonl_to_sqlite.py`) loads any existing `events.jsonl` into
SQLite; it is idempotent (`INSERT OR IGNORE` on `event_id`).

### Memory write authorization (PRD 17.6)

Privileged memory write tools must not be freely invoked by an LLM. The
following functions are gated behind an API-key dependency
(`shared/auth/api_key.py`):

- `register_trusted_capability()`
- `record_human_approval()`
- `record_validation_result()`

The dependency reads `MEMORY_WRITE_API_KEY` from the environment:

- **unset** → dev/demo mode, writes are allowed (used by the test suite).
- **set** → every write request must present a matching token via the
  `X-API-Key` header or `Authorization: Bearer <token>`.
- missing token → `401 Unauthorized`; wrong token → `403 Forbidden`.

Token comparison uses `secrets.compare_digest` to avoid timing attacks.

## Event Types

Current implemented runtime events:

- `TRIAGE_COMPLETED`
- `CAPABILITY_LOOKUP_COMPLETED`
- `RPA_WRITEBACK_COMPLETED`
- `VALIDATION_COMPLETED`
- `API_EXECUTION_COMPLETED`
- `CAPABILITY_GAP_RECORDED`
- `CAPABILITY_REGISTERED`
- `HUMAN_APPROVAL_COMPLETED`
- `READINESS_ASSESSED`

## Capability Evolution Loop

PRD 18.2 describes how the system accumulates new automation capabilities from
repeated case patterns. The trigger lives in the reasoning-agent:

1. A capability gap is recorded (e.g. `POST /capability-gaps/inventory-shortage`).
2. `count_repeated_gaps(business_action)` returns the number of gap events for
   the uncovered business action, backed by the SQLite `event_type` index.
3. When the count reaches the configurable threshold
   (`CAPABILITY_EVOLUTION_THRESHOLD`, default `3`), `run_plan_agent` is invoked
   to draft a modernization proposal.
4. The proposal is a recommendation only. Per PRD 18.5 it still requires human
   review, validation, and registration before any new workflow/API/skill is
   reused.

See [capability-evolution-loop.md](capability-evolution-loop.md) for the full
flow, interface definition, and examples.

## Boundary

The system does not automatically generate, approve, publish, or execute new XAML. Unsupported work is recorded as a capability gap for governed follow-up. The Capability Evolution Loop proposes capabilities; it never auto-deploys them.
