# Video Script

Target length: 3 to 5 minutes.

## 1. Problem

Legacy ERP work is not usually a clean API. It is a queue, an exception reason,
buyer notes, approvals, and fragile browser actions. The demo shows how UiPath
can keep running that process while an agent helps decide and document when
modernization is justified.

## 2. ERP Work Queue

Open:

```text
http://localhost:8002/erp/work-queue
```

Show the ERP detail page. The main area is business-facing: PO number, amount,
budget, vendor, ERP status, system message, and business remarks. Technical
audit fields are still present for selector compatibility but are not the main
business view.

## 3. Agent Context Trace

Open:

```text
http://localhost:8002/demo/agent-context-trace
```

Explain that UiPath calls:

```text
POST http://localhost:8002/case-intake/route
```

Show:

- `business_remarks`
- `agent_context_policy`
- company context used
- `agent_reasoning_summary`
- `llm_validation_proof`
- `recommended_erp_action`

## 4. UiPath Branching

Show route examples:

- PO-1000: deterministic standard processing.
- PO-1001: budget exception, enterprise context, human approval.
- PO-1002: missing vendor data, wait for vendor info.
- PO-1003: inventory shortage, capability gap.
- PO-1004: ambiguous justification, manual investigation.

## 5. Memory And Proposal Trigger

Open:

```text
http://localhost:8002/simulation/dashboard
```

Show Run Memory count, Pattern Memory, observed count, threshold, agent analysis
summary, and proposal pipeline. Explain that proposals are triggered by
accumulated memory, not by a manual proposal button.

## 6. Human Approval And Codex

Open:

```text
http://localhost:8002/proposals/inbox
```

Approve a proposal. Show the Codex session timeline:

```text
http://localhost:8002/codex/sessions/CODEX-PROP-API-DEMO-0001-001
```

Explain the safety boundary: human approval starts the handoff, but there is no
automatic deployment, trusted capability registration, or Windows XAML
modification.
