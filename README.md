# Agentic ERP Modernization Layer

RPA-first ERP Worker demo for modernizing legacy ERP automation safely. UiPath
continues to operate the legacy browser UI, while a coded agent service reads
enterprise context, explains routing decisions, records run memory, detects
repeatable patterns, and produces human-reviewed modernization proposals.

## Project Description

This project solves a common enterprise automation problem: many ERP workflows
start as fragile UI automation because the ERP has no clean API. The demo shows
how to keep UiPath in control of the live business process while using an agent
and memory layer to decide when a repeated RPA pattern is ready for API
modernization or when a missing workflow should become a new XAML proposal.

## What The Demo Shows

The main scenario is purchase-order exception handling in a legacy ERP:

1. UiPath opens a work queue and extracts PO fields from the ERP UI.
2. The reasoning agent receives the order, system exception, buyer remarks, and
   enterprise context.
3. The agent recommends a route, policy gate, explanation, and future-compatible
   ERP action selector.
4. UiPath keeps the execution governance: standard processing, waiting for
   vendor info, manual investigation, human approval, or capability gap.
5. Run Memory records the ERP fields, business remarks, context snapshot, agent
   decision, policy gate, selected ERP action, and final branch result.
6. Pattern Memory aggregates repeated cases by business process signature.
7. When the default threshold of 3 real observations is reached, the system
   creates a proposal:
   - `API_MODERNIZATION_PROPOSAL` for repeated budget approval patterns.
   - `XAML_WORKFLOW_PROPOSAL` for repeated inventory shortage workflow gaps.
8. A human can approve a proposal for Codex handoff. The UI then shows a
   readable Codex CLI timeline. Demo mock mode is supported, and real local
   `codex exec` mode remains available behind an explicit switch.

The safety boundary is intentional: the system does not auto-approve, deploy
APIs automatically, auto-register trusted capabilities, or auto-modify Windows
XAML.

## Repository Map

| Path | Purpose |
| --- | --- |
| `uipath-workflows/AgenticErpMvpRpa/` | UiPath Studio project entry files, including `Main.xaml`, `project.json`, and `entry-points.json`. |
| `reasoning-agent/` | FastAPI coded agent service, enterprise context API, route decision API, ERP work queue UI, approvals, dashboards, proposal inbox, and Codex handoff UI. |
| `mock-legacy-erp/` | Mock ERP support service and legacy purchase-order data. |
| `generated-api-facade/` | Candidate API facade used to demonstrate an approved API-mode target. |
| `validation-suite/` | Validation and memory query service for RPA/API parity and audit evidence. |
| `memory/` | Structured Run Memory, Pattern Memory, proposals, and seeded demo evidence. |
| `shared/automation_memory/` | Shared append-only memory and validation support code. |
| `scripts/` | Local setup, reset, smoke test, evidence collection, and demo seed scripts. |
| `docs/` | Architecture notes, judging material, runbooks, and evidence pack files. |

## Runtime Services

| Service | Port | Key role |
| --- | ---: | --- |
| Mock Legacy ERP | `8001` | Legacy purchase-order support service. |
| Reasoning Agent | `8002` | Main demo UI, agent routing, company context, memory, proposals, approvals. |
| Generated API Facade | `8003` | Candidate API execution target after validation. |
| Validation Suite | `8004` | RPA/API parity checks and read-only memory evidence APIs. |

Important URLs after startup:

- `http://localhost:8002/erp/work-queue`
- `http://localhost:8002/demo/agent-context-trace`
- `http://localhost:8002/simulation/dashboard`
- `http://localhost:8002/proposals/inbox`
- `http://localhost:8002/approvals/inbox`
- `http://localhost:8002/company-context`
- `http://localhost:8002/case-dashboard/CASE-DEMO-AGENT-CONTEXT?run_id=RUN-DEMO-AGENT-CONTEXT-001`

## UiPath Components

The submitted UiPath project is in
`uipath-workflows/AgenticErpMvpRpa/`.

Used in this solution:

| UiPath component | How it is used |
| --- | --- |
| UiPath Studio | Opens and runs the Windows project `AgenticErpMvpRpa`. |
| UiPath Robot / desktop execution | Runs the ERP Worker against the local browser-based ERP. |
| `Main.xaml` | Main RPA-first ERP Worker workflow. It drives the work queue, extracts order fields, calls the agent service, and follows the returned route. |
| UI Automation activities | Browser interaction with stable legacy ERP selectors for queue/detail pages and ERP action buttons. |
| WebAPI / HTTP Request activities | Calls the local FastAPI services for routing, run memory, approvals, validation, and API facade execution. |
| System activities | JSON parsing, variables, control flow, logging, and workflow orchestration. |
| Stable selectors | The ERP pages preserve selectors such as `ctl00_MainContent_grdPoWorkQueue`, `ctl00_MainContent_lblPoNumber`, `ctl00_MainContent_lblBusinessRemarks`, and existing action button selectors. |

UiPath package dependencies declared in `project.json`:

- `UiPath.System.Activities` `[26.6.0]`
- `UiPath.UIAutomation.Activities` `[26.4.4-preview]`
- `UiPath.WebAPI.Activities` `[2.5.0-preview]`

Not required for this local judging build:

| UiPath component | Status |
| --- | --- |
| Agent Builder | Not packaged. The agent is implemented as a coded Python service and invoked by UiPath over HTTP. |
| Maestro | Not required. Stage orchestration is represented by `Main.xaml` plus local service state. |
| UiPath Coded Agents | Not packaged as a UiPath Coded Agent artifact. The repository uses coded agent services in Python. |
| UiPath API Workflows | Not required. The candidate API path is represented by `generated-api-facade` and called by UiPath through HTTP Request activities. |
| Action Center | Not required. Human approval is demonstrated by the local `/approvals/inbox` web UI. |
| Orchestrator Queues | Not required. The local work queue is exposed by `/erp/work-queue` for repeatable judging. |

## Agent Type

This solution uses a hybrid automation pattern:

- Coded agent: yes. `reasoning-agent` is a Python FastAPI coded agent service.
  It supports schema-validated routing, enterprise context lookup, LLM-backed or
  mock LLM decision proof, Run Memory, Pattern Memory, proposal generation, and
  Codex handoff evidence.
- Low-code UiPath workflow: yes. `Main.xaml` is the low-code RPA orchestration
  layer that controls the browser UI and business execution path.
- Low-code Agent Builder agent: no. The local demo does not require an Agent
  Builder package.

In short: the project combines a coded agent service with a UiPath low-code RPA
workflow. It does not depend on a low-code Agent Builder agent.

## Agent Runtime And Libraries

The coded agent service uses LangGraph:

- `StateGraph` models the guarded decision path for intake, context lookup,
  route planning, validation, and fallback handling.
- `MemorySaver` is used as a case-level agent checkpoint layer so the agent
  state can be inspected or resumed around human approval steps.
- Structured Run Memory under `memory/runs/`, `memory/patterns/`, and
  `memory/proposals/` remains the audit system of record. LangGraph checkpoint
  memory is supporting agent state, not the compliance ledger.

## Enterprise Context And Agent Decisioning

The enterprise context API returns a mock company snapshot:

- company goals
- Q4 context
- finance policy
- sales risk context
- operations context

`POST /case-intake/route` accepts ERP order fields plus:

- `business_remarks`
- `agent_context_policy=fetch_enterprise_context_before_decision`

When `agent_required=true`, the agent decision includes:

- `agent_context_used`
- `company_context_reference`
- `agent_reasoning_summary`
- `llm_validation_proof`
- `recommended_erp_action`
- legacy-compatible `final_route` and `policy_gate`

For deterministic precheck cases, such as normal PO processing, the response
keeps the distinction clear with `precheck_decision_source=deterministic_rule`
and `agent_required=false`.

## Demo Cases

The canonical cases cover the full flow:

| PO | Exception | Expected route |
| --- | --- | --- |
| `PO-1000` | Normal purchase | Deterministic precheck, no agent required, standard processing. |
| `PO-1001` | Budget exceeded | Agent reads company context and routes to human approval or API modernization evidence. |
| `PO-1002` | Vendor information missing | Waiting for vendor information, no modernization proposal. |
| `PO-1003` | Inventory shortage | Capability gap, repeated cases trigger `XAML_WORKFLOW_PROPOSAL`. |
| `PO-1004` | Ambiguous business justification | Manual investigation. |

Proposal thresholds are based on real Run Memory patterns, not a manual
"create proposal" button. The default threshold is `3`, configurable with:

- `PROPOSAL_THRESHOLD`
- `CAPABILITY_EVOLUTION_THRESHOLD`

Pattern grouping uses:

`business_action + exception_type + route_family + policy_gate_family + side_effects_signature`

## Setup For Judges

### 1. Prerequisites

- Python 3.11
- Git
- Windows with UiPath Studio installed, if running the RPA workflow
- Chrome or Edge available for UI automation
- Optional: DeepSeek API key for real LLM mode
- Optional: Codex CLI for real proposal handoff mode

The backend services are designed to run locally on Linux or WSL. They bind to
`0.0.0.0` so a Windows browser and UiPath Studio can reach them through
`localhost`.

### 2. Install Python Dependencies

```bash
git clone https://github.com/wanghaowei06-ui/agentic-erp-modernization-layer.git
cd agentic-erp-modernization-layer
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
chmod +x scripts/*.sh
```

### 3. Configure Demo Environment

For a repeatable judging run without external model calls:

```bash
cp .env.example .env
printf '\nLLM_DEMO_MODE=mock_success\nCODEX_CLI_DEMO_MODE=mock\n' >> .env
```

For real LLM-backed routing, set these values in `.env` and leave
`LLM_DEMO_MODE` unset:

```bash
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-flash
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=your-real-key
```

For real local Codex CLI handoff after a human approves a proposal, set:

```bash
CODEX_CLI_EXECUTION_MODE=real
```

For deterministic video recording, keep:

```bash
CODEX_CLI_DEMO_MODE=mock
```

### 4. Reset Demo Data

Reset the local ERP/API fixture data:

```bash
./scripts/reset_demo_data.sh
```

Seed the historical memory evidence used by dashboards:

```bash
.venv/bin/python scripts/seed_historical_memory.py
```

The repository also includes curated demo Run Memory and proposal files under
`memory/` so the visual dashboard can be reviewed immediately after startup.

### 5. Start Services

```bash
./scripts/dev-start.sh
```

Expected services:

- `http://localhost:8001`
- `http://localhost:8002`
- `http://localhost:8003`
- `http://localhost:8004`

Stop services with:

```bash
./scripts/dev-stop.sh
```

### 6. Open The UiPath Project

In UiPath Studio, open:

```text
uipath-workflows/AgenticErpMvpRpa/project.json
```

Run:

```text
Main.xaml
```

The workflow is expected to interact with:

```text
http://localhost:8002/erp/work-queue
```

The repo intentionally includes only the UiPath project entry files needed for
review. Windows Studio runtime caches, screenshots, backup XAML files, and local
object folders are not committed.

### 7. Click-Through Demo Path

Use browser clicks for the clearest judging flow:

1. Open `http://localhost:8002/erp/work-queue`.
2. Open the first pending ERP work item and show that the page looks like a
   real purchase-order screen while preserving stable UiPath selectors.
3. Open `http://localhost:8002/demo/agent-context-trace` to show the agent
   trace using enterprise context, buyer remarks, and policy gates.
4. Open `http://localhost:8002/case-dashboard/CASE-DEMO-AGENT-CONTEXT?run_id=RUN-DEMO-AGENT-CONTEXT-001`
   to show single-run evidence.
5. Open `http://localhost:8002/simulation/dashboard` to show Run Memory,
   Pattern Memory, observed counts, thresholds, and proposal pipeline.
6. Inject or process repeated budget-exceeded cases until the pattern reaches
   threshold `3`; show `API_MODERNIZATION_PROPOSAL`.
7. Inject or process repeated inventory-shortage cases until the pattern
   reaches threshold `3`; show `XAML_WORKFLOW_PROPOSAL`.
8. Open `http://localhost:8002/proposals/inbox`.
9. Approve a proposal for Codex handoff. The Codex session page shows either a
   staged mock stream or real local Codex CLI mode, depending on environment.
10. Open `http://localhost:8002/approvals/inbox` to show human approval tasks
    with PO summary, business remarks, agent reasoning, company context, and
    approve/reject controls.

## Verification

Run the focused reasoning-agent suite:

```bash
PYTHONPATH=$PWD .venv/bin/pytest reasoning-agent/tests -q
```

Run the broader local suite:

```bash
PYTHONPATH=$PWD .venv/bin/pytest reasoning-agent/tests generated-api-facade/tests validation-suite/tests shared/automation_memory/tests shared/auth/tests -q
```

Run smoke and evidence collection:

```bash
./scripts/smoke-test.sh
./scripts/collect-demo-evidence.sh
```

## Safety And Governance

The project keeps these controls visible:

- UiPath selectors are preserved for existing robot compatibility.
- ERP action buttons do not create proposals or call Codex by themselves.
- Normal deterministic cases are not disguised as LLM decisions.
- Agent-required cases show context usage and LLM validation proof.
- Human approval is required for approval tasks and proposal handoff.
- Modernization proposals are proposal-only until reviewed.
- No Windows XAML modification happens automatically.
- No API deployment or trusted capability registration happens automatically.

## Current Limitations

- The ERP and company context are local mocks for judging.
- Real LLM mode requires external credentials.
- Real Codex CLI mode requires local Codex CLI setup and explicit environment
  configuration.
- The local approval inbox represents the human-in-the-loop pattern; it is not a
  production Action Center deployment.
