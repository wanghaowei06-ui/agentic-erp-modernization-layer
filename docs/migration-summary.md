# Migration Summary

This repository demonstrates an Agentic ERP Modernization Layer around a
UiPath-first ERP worker. It does not replace UiPath. It helps UiPath decide
which ERP button to click, when a human approval task is required, and when
repeated RPA patterns should become modernization proposals.

## Before

The ERP exception process is a browser-driven workflow. UiPath reads purchase
order fields and clicks stable ERP controls.

## Current Layer

UiPath now calls `POST /case-intake/route` with ERP fields, system exception
text, business remarks, and an agent context policy. Agent-required cases fetch
mock enterprise context from `/company-context` and return:

- `final_route`
- `policy_gate`
- `agent_reasoning_summary`
- `llm_validation_proof`
- `recommended_erp_action`

## Candidate Modernization

Modernization is evidence-driven. Run Memory records each UiPath run. Pattern
Memory aggregates repeated runs by business action, exception type, route
family, policy gate family, and side-effect signature. When threshold is
reached, the system can create:

- `API_MODERNIZATION_PROPOSAL`
- `XAML_WORKFLOW_PROPOSAL`

The proposals still require human approval before Codex handoff. No deployment,
trusted registration, or Windows XAML modification is automatic.
