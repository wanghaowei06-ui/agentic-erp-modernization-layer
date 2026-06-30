# Demo Evidence Page IDs And URLs

These pages support screenshots and review. They do not replace UiPath
orchestration.

## Primary Pages

- ERP Work Queue: `http://localhost:8002/erp/work-queue`
- Agent Context Trace: `http://localhost:8002/demo/agent-context-trace`
- Pattern Memory Dashboard: `http://localhost:8002/simulation/dashboard`
- Approval Inbox: `http://localhost:8002/approvals/inbox`
- Proposal Inbox: `http://localhost:8002/proposals/inbox`
- Company Context API: `http://localhost:8002/company-context`

## Case Dashboard

Use:

```text
http://localhost:8002/case-dashboard/CASE-DEMO-AGENT-CONTEXT?run_id=RUN-DEMO-AGENT-CONTEXT-001
```

The page shows:

- ERP order fields
- business remarks
- company context used
- agent decision
- policy gate
- UiPath RPA action
- memory commit
- pattern update

## Proposal / Codex Evidence

Use:

```text
http://localhost:8002/proposals/inbox
http://localhost:8002/codex/sessions/CODEX-PROP-API-DEMO-0001-001
http://localhost:8002/codex/sessions/CODEX-PROP-XAML-DEMO-0001-001
```

The Codex session page can show either a demo mock stream or real local Codex
CLI mode, depending on environment settings. It starts only after a human
approves a proposal.
