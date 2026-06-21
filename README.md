# Agentic ERP Modernization Layer

A UiPath-governed modernization case that turns fragile legacy ERP clicks into validated, human-approved API tools for enterprise agents.

This repository contains local demo support assets only. UiPath creates and governs the modernization case. UiPath RPA extracts data from the legacy ERP UI. UiPath invokes the exception triage service. UiPath routes the case. UiPath handles human approval. UiPath governs validation and tool registration. UiPath API Workflow switches execution from RPA to API.

## Services

| Service | Port | Purpose |
| --- | ---: | --- |
| `mock-legacy-erp` | 8000 | Legacy ERP UI for UiPath RPA scraping and clicking |
| `reasoning-agent` | 8001 | Deterministic exception triage support service |
| `generated-api-facade` | 8002 | Validated API facade candidate for approval requests |
| `validation-suite` | 8003 | Validation support service before trusted-tool registration |

All services bind to `0.0.0.0` so UiPath on Windows can call them through `localhost` when the repo runs in WSL2 Ubuntu.

## Setup

```bash
cd /home/changv/projects/Uipath
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x scripts/start_all.sh scripts/smoke_test.sh scripts/reset_demo_data.sh
```

## Start All Services

```bash
./scripts/start_all.sh
```

Windows/UiPath URLs:

- `http://localhost:8000`
- `http://localhost:8001`
- `http://localhost:8002`
- `http://localhost:8003`

You can also start each service directly from its module directory:

```bash
cd mock-legacy-erp
uvicorn app.main:app --host 0.0.0.0 --port 8000

cd reasoning-agent
uvicorn app.main:app --host 0.0.0.0 --port 8001

cd generated-api-facade
uvicorn app.main:app --host 0.0.0.0 --port 8002

cd validation-suite
uvicorn app.main:app --host 0.0.0.0 --port 8003
```

## Smoke Tests

```bash
./scripts/smoke_test.sh
```

The smoke test checks health endpoints, enhanced demo pages, PO-1001/PO-1002/PO-1003 triage, the validation gate, the validation failed simulation, the generated API facade, and local demo reset.

## Curl Examples

```bash
curl http://localhost:8000/health
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
```

```bash
curl -sS -X POST http://localhost:8001/triage \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "CASE-001",
    "po_id": "PO-1001",
    "amount": 18000,
    "budget_limit": 10000,
    "vendor_id": "V-203",
    "vendor_info_complete": true,
    "inventory_available": true,
    "erp_status": "Exception",
    "raw_exception_text": "Amount exceeds approved budget limit"
  }'
```

```bash
curl -sS -X POST http://localhost:8001/triage \
  -H "Content-Type: application/json" \
  -d '{
    "case_id": "CASE-003",
    "po_id": "PO-1003",
    "amount": 8500,
    "budget_limit": 10000,
    "vendor_id": "V-118",
    "vendor_info_complete": true,
    "inventory_available": false,
    "erp_status": "Exception",
    "raw_exception_text": "Inventory shortage"
  }'
```

```bash
curl -sS -X POST http://localhost:8003/validate/request-purchase-order-approval
```

```bash
curl -sS -X POST http://localhost:8003/validate/request-purchase-order-approval \
  -H "Content-Type: application/json" \
  -d '{"simulate_failure": true}'
```

```bash
curl -sS -X POST http://localhost:8002/api/purchase-orders/PO-1001/approval-request \
  -H "Content-Type: application/json" \
  -d '{
    "approval_reason": "Amount exceeds budget limit",
    "manager_id": "MGR-001",
    "source_case_id": "CASE-001"
  }'
```

## Enhanced MVP Support Pages

These pages are local demo evidence surfaces. They do not orchestrate the business process; UiPath remains the main orchestration and governance layer.

- Case Dashboard: `http://localhost:8000/case-dashboard`
- Case Timeline: `http://localhost:8000/case-timeline/CASE-001`
- API Readiness Scorecard: `http://localhost:8000/api-readiness-scorecard`
- Tool Registry: `http://localhost:8000/tool-registry`
- Timeline JSON: `http://localhost:8000/api/demo/cases/CASE-001/timeline`
- Scorecard JSON: `http://localhost:8000/api/demo/api-readiness-scorecard`
- Tool Registry JSON: `http://localhost:8000/api/demo/tool-registry`
- Local demo reset: `POST http://localhost:8000/api/demo/reset`

To reset local demo data from WSL2:

```bash
./scripts/reset_demo_data.sh
```

## Demo Flow

1. Open `http://localhost:8000/purchase-orders` in a Windows browser. PO-1001, PO-1002, and PO-1003 are visible.
2. UiPath RPA opens a purchase order detail page and scrapes stable element IDs such as `po-id`, `amount`, `budget-limit`, and `raw-exception-text`.
3. UiPath calls `POST http://localhost:8001/triage` with scraped fields.
4. UiPath routes the case based on `detected_exception_type`, `risk_level`, and `requires_human_approval`.
5. UiPath handles any human approval.
6. For the legacy path, UiPath RPA writes back by filling `approval-reason-input`, `manager-id-input`, and clicking `request-approval-button`.
7. UiPath calls `POST http://localhost:8003/validate/request-purchase-order-approval`.
8. If validation passes and registration is approved, UiPath can switch the approved case to API mode.
9. UiPath calls `POST http://localhost:8002/api/purchase-orders/{po_id}/approval-request`.
10. For enhanced evidence, UiPath or the demo operator can show dashboard, timeline, scorecard, registry, and validation failure simulation pages.

## UiPath Implementation Pack

UiPath builder assets are in [uipath-workflows/README.md](/home/changv/projects/Uipath/uipath-workflows/README.md). The folder includes workflow outlines, variable tables, request bodies, expected outputs, selector notes, troubleshooting, runbook material, and a template-only XAML skeleton.

## What the human builder still configures in UiPath

- Create UiPath process / app / case-style flow
- Configure browser automation
- Pick UI elements in Chrome
- Configure HTTP Request activities
- Configure human approval steps
- Run attended robot
- Capture UiPath screenshots/video

## What Codex Generated

Codex generated the non-UiPath support assets in this repository: mock UI, deterministic triage service, API facade candidate, validation service, tests, scripts, documentation, and UiPath implementation aids.

Codex did not implement the main workflow orchestration. Codex is not triggered at runtime by UiPath in this demo. Codex did not modify production ERP code.

## Tests

```bash
source .venv/bin/activate
pytest mock-legacy-erp reasoning-agent generated-api-facade validation-suite
```

## Scope Notes

This is a local hackathon MVP. It does not claim full ERP modernization, production deployment, or production ERP code modification.
