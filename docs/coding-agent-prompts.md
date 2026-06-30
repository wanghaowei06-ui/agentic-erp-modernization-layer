# Coding Agent Prompts

Codex owns the Linux backend, tests, docs, sample evidence, and proposal
handoff scaffolding in this repository. UiPath Studio / Windows XAML authoring
remains a governed human activity unless a user explicitly approves a proposal
handoff.

## Current Backend Prompt Summary

Key boundaries:

- Do not silently edit Windows XAML logic.
- Preserve UiPath selectors and existing endpoint compatibility.
- Keep `/case-intake/route` as the current route-agent contract.
- Keep `/triage` only as compatibility, not the main demo path.
- Route normal cases with deterministic precheck and label them as deterministic.
- Route exception and ambiguous cases through the enterprise-context agent path.
- Show `business_remarks`, company context usage, `llm_validation_proof`, and recommended ERP action in responses and dashboards.
- Generate modernization proposals only from repeated Run Memory / Pattern Memory threshold evidence.
- Do not auto-approve proposals, auto-call Codex, deploy APIs, register trusted capabilities, or modify Windows XAML.

Prompted deliverables:

- Service ports: mock ERP `8001`, reasoning agent / ERP worker `8002`, generated API facade `8003`, validation suite `8004`.
- `GET /health` on every service.
- `GET /company-context`.
- `POST /case-intake/route`.
- `/approvals/inbox`, `/simulation/dashboard`, `/proposals/inbox`, and Codex session monitor pages.
- UiPath workflow pack under `uipath-workflows/AgenticErpMvpRpa/`.
- Evidence scripts and JSON samples aligned with the current route-agent contract.
