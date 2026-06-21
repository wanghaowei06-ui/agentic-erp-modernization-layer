# UiPath Implementation Pack

This folder contains UiPath implementation assets for the Agentic ERP Modernization Layer hackathon MVP. These files are templates, request bodies, expected outputs, selector notes, demo scripts, and runbook material for the human builder in UiPath Studio.

UiPath is the case orchestration layer. UiPath owns the case lifecycle, dynamic routing, governance, approval handling, RPA execution, validation gate, trusted-tool approval, and API-mode execution decision.

The Python services in this repository are callable support assets only:

- Mock legacy ERP UI for UiPath RPA extraction and write-back.
- Deterministic exception triage service called by UiPath over HTTP.
- Validation support service called by UiPath before trusted-tool registration.
- Generated API facade candidate called by UiPath only after validation and approval.

The final UiPath flow should show these stages:

1. Case Intake
2. Legacy ERP Extraction
3. Exception Triage
4. Dynamic Routing
5. Human Approval
6. Legacy RPA Write-back
7. Validation Gate
8. Trusted Tool Approval
9. API Mode Execution

Codex does not run at UiPath runtime in this demo, and this folder does not move orchestration into Python.
