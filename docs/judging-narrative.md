# Judging Narrative

## What Problem Does This Solve?

Enterprise ERP exception work often starts in browser-based queues, not clean
APIs. Business staff and robots need order fields, system messages, and buyer
notes, while modernization teams need evidence before replacing RPA with APIs or
new workflows.

## What Is UiPath's Role?

UiPath remains the RPA-first orchestration and execution layer. It opens the ERP
work queue, extracts fields, calls the route agent, creates approval tasks,
clicks safe ERP action buttons, writes Run Memory, and keeps humans in control.

## What Is The Agent's Role?

The coded LangGraph agent receives ERP fields, system exception text, business
remarks, and mock enterprise context. It returns:

- `final_route`
- `policy_gate`
- `agent_reasoning_summary`
- `company_context_reference`
- `llm_validation_proof`
- `recommended_erp_action`

Normal cases are deterministic precheck decisions. Agent-required cases show
whether real or mock LLM mode was used.

## Why Enterprise Context?

The agent decision is not just a deterministic amount check. For example,
PO-1001 combines a budget exception with Q4 customer delivery risk, finance
policy, and strategic account pressure. That is why the demo shows the context
signals used and the reasoning summary.

## Why Memory?

Run Memory is the per-run evidence trail. Pattern Memory aggregates repeated
business signatures. This is what makes proposals evidence-driven instead of
button-driven.

## Why Proposal Thresholds?

The system should not modernize a workflow after a single exception. It waits
until repeated committed runs reach the threshold, then creates either:

- `API_MODERNIZATION_PROPOSAL`
- `XAML_WORKFLOW_PROPOSAL`

The proposal remains review-only until a human approves it.

## Why Codex Handoff?

After human approval, the proposal can start a Codex handoff session. The UI
shows a readable timeline. Demo mock mode is available for recording; real local
Codex CLI mode remains behind an explicit environment switch.

## Core Claim

This project demonstrates a safe, auditable modernization loop where UiPath
continues to operate the ERP process, the agent adds enterprise context and
reasoning, memory accumulates evidence, and modernization begins only after a
human approves a proposal.
