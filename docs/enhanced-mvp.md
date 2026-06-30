# Current Demo Surfaces

This document tracks the website-clickable surfaces used by the current
RPA-first ERP Worker demo. Earlier "Enhanced MVP" pages have been folded into
the main recording path on port `8002`.

## Implemented

- ERP Work Queue: `GET http://localhost:8002/erp/work-queue`
- ERP Detail: `GET http://localhost:8002/erp/work-queue/{simulation_case_id}`
- Enterprise Context: `GET http://localhost:8002/company-context`
- Route Agent Contract: `POST http://localhost:8002/case-intake/route`
- Agent Context Trace: `GET http://localhost:8002/demo/agent-context-trace`
- Single Run Evidence: `GET http://localhost:8002/case-dashboard/{case_id}?run_id=...`
- Approval Inbox: `GET http://localhost:8002/approvals/inbox`
- Pattern Memory Dashboard: `GET http://localhost:8002/simulation/dashboard`
- Proposal Inbox: `GET http://localhost:8002/proposals/inbox`
- Codex Session Monitor: `GET http://localhost:8002/codex/sessions/{session_id}`
- Evidence Snapshot: `GET http://localhost:8002/demo/evidence-snapshot`

## Main Narrative

UiPath opens the ERP work queue, extracts order fields and business remarks, and
calls the route agent. The agent-required path reads mock enterprise context,
uses LangGraph/LLM-backed reasoning in configured real or mock mode, returns a
policy gate and recommended ERP action, and writes evidence through Run Memory.

Pattern Memory aggregates repeated completed runs. Proposals appear only after
the observed count reaches threshold. Proposal approval is the human-controlled
handoff point for Codex.

## Legacy Support

Older support endpoints such as `/triage` and the mock legacy ERP PO pages on
port `8001` remain for compatibility tests and historical evidence. They are
not the primary recording path.
