# Demo Script

This script describes the current website-clickable demo path.

## 1. Start Services

```bash
./scripts/dev-start.sh
```

Confirm health:

- `http://localhost:8001/health`
- `http://localhost:8002/health`
- `http://localhost:8003/health`
- `http://localhost:8004/health`

## 2. Open ERP Work Queue

Open:

```text
http://localhost:8002/erp/work-queue
```

Show the queue and then an order detail page. Point out business remarks and
stable UiPath selectors.

## 3. Show Agent Context Trace

Open:

```text
http://localhost:8002/demo/agent-context-trace
```

Narrate the trace from UiPath extraction through route-agent decision, company
context, policy gate, recommended ERP action, and memory commit.

## 4. Show Single-Run Evidence

Open:

```text
http://localhost:8002/case-dashboard/CASE-DEMO-AGENT-CONTEXT?run_id=RUN-DEMO-AGENT-CONTEXT-001
```

Show ERP fields, business remarks, company context used, agent decision, policy
gate, UiPath action, memory commit, and pattern update.

## 5. Show Approval Inbox

Open:

```text
http://localhost:8002/approvals/inbox
```

Show PO number, amount/budget, system message, business remarks, agent
recommendation, company context snapshot, and approve/reject controls.

## 6. Show Pattern Dashboard

Open:

```text
http://localhost:8002/simulation/dashboard
```

Show Run Memory count, Pattern Memory, observed count / threshold, agent
analysis summary, and proposal pipeline.

## 7. Show Proposal Pipeline

Open:

```text
http://localhost:8002/proposals/inbox
```

Show `API_MODERNIZATION_PROPOSAL` and `XAML_WORKFLOW_PROPOSAL`. Explain they
come from repeated Pattern Memory evidence.

## 8. Approve Proposal And Show Codex Handoff

Click approval for a proposal, then show:

```text
http://localhost:8002/codex/sessions/CODEX-PROP-API-DEMO-0001-001
```

In mock mode this is a staged stream for video clarity. In real mode it attempts
local Codex CLI after explicit human approval.
