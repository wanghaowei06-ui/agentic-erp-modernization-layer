# Agentic ERP Modernization Layer

A UiPath-governed modernization demo that shows a safe path from fragile legacy ERP RPA to validated, human-approved API execution.

The Hard MVP is deliberately stable: UiPath orchestrates the case, robots read and write the legacy UI, the reasoning service returns deterministic structured decisions, validation gates API mode, and Automation Memory records the audit trail. LLM structured triage is an Enhanced Mode option, not a Hard MVP dependency.

## Project Overview

Many ERP automations start as UI workflows because legacy systems do not expose clean APIs. This project demonstrates a governed modernization pattern:

```text
UiPath RPA observes legacy behavior
-> reasoning-agent returns a structured decision
-> UiPath routes and keeps human approval in control
-> RPA writes back to the legacy ERP
-> validation-suite checks contract, rule, and RPA/API parity
-> generated-api-facade executes the approved API path
-> Automation Memory records decisions, traces, validations, capabilities, and gaps
```

This is a local hackathon MVP, not a production ERP deployment. It does not auto-generate or auto-publish XAML, does not auto-register production tools, and does not replace human approval.

## Hard MVP Runtime Contracts

| Service | Port | Main contract |
| --- | ---: | --- |
| Mock Legacy ERP | `8001` | `GET /purchase-orders`, `POST /purchase-orders/{po_id}/request-approval` |
| reasoning-agent | `8002` | `POST /triage` |
| generated-api-facade | `8003` | `POST /api/purchase-orders/{po_id}/approval-request` |
| validation-suite | `8004` | `POST /validate/request-purchase-order-approval`, memory read APIs |

Health checks:

- `GET http://localhost:8001/health`
- `GET http://localhost:8002/health`
- `GET http://localhost:8003/health`
- `GET http://localhost:8004/health`

## Demo Flow

1. Start the four local support services.
2. UiPath opens Mock Legacy ERP and reads PO-1001.
3. UiPath calls `POST http://localhost:8002/triage`.
4. The reasoning-agent returns `detected_exception_type=budget_exceeded` using deterministic structured rules.
5. The triage result is written to Automation Memory as `TRIAGE_COMPLETED`.
6. UiPath routes to Human Approval.
7. UiPath performs RPA write-back in the legacy ERP.
8. Mock ERP writes `RPA_WRITEBACK_COMPLETED`.
9. UiPath calls validation-suite.
10. validation-suite writes `VALIDATION_COMPLETED` and registers trusted capabilities after passed validation.
11. UiPath calls generated-api-facade.
12. generated-api-facade writes `API_EXECUTION_COMPLETED`.
13. PO-1003 demonstrates `CAPABILITY_GAP_RECORDED` instead of uncontrolled automation.
14. The memory query API shows the case timeline, trusted capabilities, and capability gaps.

## Agent Layer

The Hard MVP agent layer is a structured decision service. It does not execute business actions.

- Default mode: `deterministic_rule`
- Current endpoint: `POST http://localhost:8002/triage`
- Stable routing field: `detected_exception_type`
- Supported types: `budget_exceeded`, `vendor_info_missing`, `inventory_shortage`, `unknown_exception`
- Governance behavior: unknown or unsupported cases route to manual investigation or capability gap handling

Enhanced Mode can add `llm_structured` or `hybrid_guarded` decisioning later, with schema validation, deterministic guardrails, and fail-closed fallback. That is not required for the Hard MVP.

## Automation Memory Layer

Automation Memory is the governed system of record, not LLM chat memory.

It records append-only events for:

- `TRIAGE_COMPLETED`
- `RPA_WRITEBACK_COMPLETED`
- `VALIDATION_COMPLETED`
- `API_EXECUTION_COMPLETED`
- `CAPABILITY_GAP_RECORDED`
- `CAPABILITY_REGISTERED`

Current storage uses the repository-first `shared/automation_memory` module with a JSON/JSONL adapter under `memory-data/`. This keeps the MVP lightweight while preserving a migration path to SQLite or PostgreSQL later. Vector or graph memory can be future retrieval layers, but they should not replace the audit source.

## Capability Registry And Capability Gap

Passed validation registers trusted capabilities in a list-based capability registry. Current demo capabilities include:

- `cap_api_request_po_approval_v1`
- `cap_human_po_approval_v1`

PO-1003 demonstrates an uncovered inventory shortage workflow. The system records a capability gap instead of allowing a model or robot to freely invent a new runtime path.

## UiPath Orchestration Role

UiPath remains the orchestration, human approval, and execution governance layer:

- Opens and reads the legacy ERP UI
- Calls HTTP support services
- Routes by `detected_exception_type`
- Owns Human Approval
- Performs RPA write-back
- Calls validation
- Calls the approved generated API
- Produces the final case output

The Python services are support services and evidence surfaces only.

## API vs RPA Modernization Path

The core business action is `request_purchase_order_approval`.

Hard MVP demonstrates:

```text
RPA baseline through Mock ERP
-> validation parity on cloned data
-> trusted capability registration evidence
-> API execution through generated-api-facade
```

The validation parity is a Hard MVP demo heuristic using cloned data (`PO-1001-RPA` and `PO-1001-API`). It is not a production-grade WebForms side-effect proof.

## Run Locally

```bash
cd /home/changv/projects/Uipath
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x scripts/*.sh
./scripts/dev-start.sh
```

Stop services:

```bash
./scripts/dev-stop.sh
```

Optional Docker Compose:

```bash
docker compose up --build
```

## Smoke Test

```bash
./scripts/smoke-test.sh
```

Expected checks include health, triage, generated API, validation, legacy memory artifacts, RPA write-back memory, timeline query, capabilities query, and gaps query.

Run pytest:

```bash
.venv/bin/python -m pytest shared/automation_memory/tests
.venv/bin/python -m pytest reasoning-agent
.venv/bin/python -m pytest validation-suite
.venv/bin/python -m pytest generated-api-facade
.venv/bin/python -m pytest mock-legacy-erp
```

## Memory Query API

The validation-suite exposes read-only memory queries on port `8004`:

- `GET http://localhost:8004/memory/cases/CASE-001`
- `GET http://localhost:8004/memory/cases/CASE-001/timeline`
- `GET http://localhost:8004/memory/decisions/CASE-001`
- `GET http://localhost:8004/memory/capabilities`
- `GET http://localhost:8004/memory/gaps`

These endpoints are read-only. They do not approve, delete, mutate, or publish capabilities.

## Demo Evidence

Generate a local evidence pack:

```bash
./scripts/collect-demo-evidence.sh
```

The script stores JSON/HTML evidence under `docs/evidence/` and writes `docs/evidence/manifest.json`.

For the release candidate freeze, run:

```bash
make demo-check
```

See:

- [Demo Freeze Checklist](docs/demo-freeze-checklist.md)
- [Release Candidate](docs/release-candidate.md)
- [Video Script](docs/video-script.md)

## UiPath Implementation Pack

UiPath builder assets are under [uipath-workflows/](/home/changv/projects/Uipath/uipath-workflows). They are implementation aids and template material. This phase does not require changing existing UiPath XAML or selectors.

## Enhanced Roadmap

Future enhancements can include:

- LLM structured triage with deterministic guardrails
- Capability lookup agent
- Readiness assessment agent
- SQLite/PostgreSQL Automation Memory backend
- UiPath Apps dashboard
- Orchestrator Queues and Action Center
- Production-grade validation with live RPA traces
- Process Mining and Insights

Do not describe roadmap items as completed runtime behavior unless they are actually implemented.
