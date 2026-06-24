# Judging Narrative

## What Problem Does This Solve?

Enterprise ERP exceptions often live in fragile UI workflows. Teams want APIs, but they cannot safely jump from RPA clicks to API execution without proving business equivalence, approval boundaries, and auditability.

## Why Not Jump Directly From RPA To API?

The UI workflow may hide side effects: status updates, approval task creation, audit logs, notifications, and budget review flags. The API path must be validated against the RPA-observed behavior before it can be trusted.

## Why Use RPA First?

RPA discovers the current legacy behavior. In this MVP, UiPath reads PO-1001, routes the case, and performs write-back before the generated API candidate is validated.

## What Is UiPath's Role?

UiPath remains the orchestration, human approval, and execution governance layer. It owns the case flow, RPA steps, human approval, validation call, API-mode call, and final output.

## What Is The Agent's Role?

The agent is a structured decision service. In the Hard MVP it uses deterministic triage for stable governance. It emits a decision object and records `TRIAGE_COMPLETED`; it does not execute business actions.

## What Is Memory's Role?

Automation Memory is a governed system of record, not chat memory. It records decisions, RPA write-back, validation, API execution, trusted capabilities, and capability gaps.

## Why Capability Registry?

The registry records which capabilities are trusted after validation and approval evidence. This supports safe reuse instead of ad hoc calls to unverified APIs or workflows.

## Why Capability Gap?

When PO-1003 hits `inventory_shortage`, the system records `CAPABILITY_GAP_RECORDED`. It does not let a model invent a runtime workflow. The gap becomes a governed proposal for future implementation.

## Why Deterministic Triage In Hard MVP?

Hard MVP uses deterministic structured triage for stability and repeatability. This is a governance choice: the demo proves the UiPath-controlled lifecycle and Automation Memory trail without relying on an external model key.

## How Can Enhanced Mode Use LLMs?

Enhanced Mode can add LLM structured triage with schema validation, deterministic guardrails, and fail-closed fallback. The output contract should remain compatible with UiPath and Automation Memory.

## Core Claim

The project demonstrates a safe path from RPA-observed legacy workflows to validated trusted API capabilities, while keeping UiPath and humans in control.
