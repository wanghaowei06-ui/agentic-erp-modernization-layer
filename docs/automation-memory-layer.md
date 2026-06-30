# Automation Memory Layer

Automation Memory is the audit and learning layer for the RPA-first ERP Worker.
It records what UiPath extracted, what the route agent decided, which enterprise
context was used, what policy gate blocked or allowed, which ERP action was
selected, and how repeated runs aggregate into modernization proposals.

## Current Stores

- `memory/runs/`: run-level event and artifact records written by the current Run Memory API.
- `memory/cases/`: case-level summaries and dashboard data.
- `memory/patterns/`: Pattern Memory grouped by business action, exception type, route family, policy gate family, and side-effect signature.
- `memory/proposals/`: governed API modernization and XAML workflow proposals.
- `memory/codex_sessions/`: human-approved Codex CLI handoff sessions.
- `memory/data/`: legacy compatibility and seeded demo evidence.

## Run Memory Fields

Current Run Memory should include:

- ERP extracted fields.
- `business_remarks`.
- company context snapshot or reference.
- route plan.
- agent reasoning summary.
- `llm_validation_proof`.
- policy gate.
- selected ERP action.
- final branch result.

## Pattern Memory

Pattern Memory does not group solely by PO ID. It uses:

```text
business_action + exception_type + route_family + policy_gate_family + side_effects_signature
```

This is what lets the demo show repeated CAPEX budget exceptions becoming an
API modernization proposal and repeated inventory shortages becoming a XAML
workflow proposal.

## Boundary

Automation Memory can create proposals after thresholds are reached, but it does
not approve them. It does not call Codex before human approval. It does not
deploy APIs, register trusted capabilities, or modify Windows XAML automatically.
