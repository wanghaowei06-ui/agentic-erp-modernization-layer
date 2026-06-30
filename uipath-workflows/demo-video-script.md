# Demo Video Script

## 0:00 - 0:30 Problem

Legacy ERP work is still trapped behind browser queues, exception screens, and
manual approval notes. The goal is not to replace UiPath. The goal is to let
UiPath keep control while an agent explains when a case should stay in RPA,
wait for business data, require approval, or become a modernization proposal.

## 0:30 - 1:20 UiPath Opens A Realistic ERP Work Queue

Show `http://localhost:8002/erp/work-queue`.

UiPath opens `Main.xaml`, reads PO fields from stable selectors, and extracts
business remarks such as buyer notes or operations notes. The visible ERP page
looks like a business order screen; technical audit fields are tucked away but
selectors are preserved.

## 1:20 - 2:10 Agent Uses Enterprise Context

Show the call to `POST http://localhost:8002/case-intake/route`.

For PO-1001, the agent sees:

- amount above budget
- system exception reason
- Q4 customer delivery business remarks
- finance policy
- strategic account context
- operations constraints

The response shows `agent_context_used=true`, company context proof,
`agent_reasoning_summary`, `llm_validation_proof`, and
`recommended_erp_action`.

## 2:10 - 3:00 UiPath Branches Safely

Show each route:

- normal case -> standard processing
- vendor info missing -> mark waiting vendor
- inventory shortage -> flag capability gap
- ambiguous case -> manual investigation
- budget exceeded -> create web approval task instead of clicking ERP approval

Open `/approvals/inbox` and show the order summary, business remarks, agent
reasoning, company context, and approve/reject controls.

## 3:00 - 4:00 Memory Accumulates Evidence

Open `/case-dashboard/CASE-DEMO-AGENT-CONTEXT?run_id=RUN-DEMO-AGENT-CONTEXT-001`
and `/simulation/dashboard`.

Show Run Memory, Pattern Memory, observed count, threshold, business remarks
examples, company context used, and why the agent chose the route.

Explain that proposals are not buttons. They appear only after repeated cases
are processed and Pattern Memory reaches the threshold.

## 4:00 - 5:00 Proposal Approval And Codex Handoff

Open `/proposals/inbox`.

Show both proposal families:

- `API_MODERNIZATION_PROPOSAL`
- `XAML_WORKFLOW_PROPOSAL`

After a human approves a proposal, show the Codex session timeline. In mock
mode it is a readable staged stream; in real mode it uses local Codex CLI.
Neither mode auto-deploys APIs, auto-registers trusted capabilities, or
auto-modifies Windows XAML.
