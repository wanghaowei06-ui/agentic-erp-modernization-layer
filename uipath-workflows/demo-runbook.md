# Demo Runbook

## 1. Start Services

From WSL/Linux:

```bash
./scripts/dev-start.sh
```

Confirm:

- `http://localhost:8001/health`
- `http://localhost:8002/health`
- `http://localhost:8003/health`
- `http://localhost:8004/health`

## 2. Open Current ERP Worker UI

Open:

```text
http://localhost:8002/erp/work-queue
```

This is the current UiPath-facing ERP work queue. The old
`http://localhost:8001/purchase-orders` page remains a support service, but it
is not the current main demo entry page.

## 3. Run UiPath Workflow

Open in UiPath Studio:

```text
uipath-workflows/AgenticErpMvpRpa/project.json
```

Run:

```text
Main.xaml
```

UiPath should:

1. Open the ERP work queue.
2. Extract order fields and business remarks.
3. Start Run Memory.
4. Call `POST http://localhost:8002/case-intake/route`.
5. Branch by `final_route` / `policy_decision`.
6. Commit Run Memory and update Pattern Memory.

## 4. Show Agent Context Trace

Open:

```text
http://localhost:8002/demo/agent-context-trace
```

Show that the trace includes:

- ERP extracted fields
- business remarks
- mock enterprise context
- agent decision
- policy gate
- recommended ERP action
- memory closure

## 5. Show Canonical Cases

Use the route request bodies under `http-request-bodies/`:

| Case | Expected result |
| --- | --- |
| `PO-1000` | `STANDARD_PROCESSING`, deterministic precheck, no LLM. |
| `PO-1001` | `WAITING_FOR_HUMAN_APPROVAL`, enterprise context used. |
| `PO-1002` | `WAITING_VENDOR_INFO`. |
| `PO-1003` | `CAPABILITY_GAP_DETECTED`, XAML proposal evidence after threshold. |
| `PO-1004` | `WAITING_MANUAL_INVESTIGATION`. |

## 6. Show Dashboards

Open:

```text
http://localhost:8002/case-dashboard/CASE-DEMO-AGENT-CONTEXT?run_id=RUN-DEMO-AGENT-CONTEXT-001
http://localhost:8002/simulation/dashboard
http://localhost:8002/approvals/inbox
http://localhost:8002/proposals/inbox
```

## 7. Show Proposal Handoff

In `/proposals/inbox`, approve a proposal for Codex handoff. The next page shows
the Codex session timeline:

```text
http://localhost:8002/codex/sessions/CODEX-PROP-API-DEMO-0001-001
```

Use mock mode for deterministic video recording, or real mode when local Codex
CLI is configured.

## 8. Reset Demo Data

Before another run:

```bash
./scripts/reset_demo_data.sh
```
