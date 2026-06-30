# UiPath Authoring Skill

This document defines future assistance for drafting UiPath workflow assets from
a business action. It is not a runtime auto-deployment feature.

## Input Parameters

A future authoring request should include:

- `business_action`
- `case_id`
- `po_id`
- source application name and URL
- expected input fields
- expected output fields
- required human approval stage
- route endpoint and expected response fields
- validation endpoint, if one exists
- API endpoint, if one exists
- exception handling requirements
- selector evidence collected by a human or UiPath author

## XAML Split

Generated drafts should separate concerns:

- `Main.xaml` for high-level orchestration handoff points
- extraction workflow for reading legacy ERP fields
- route-agent HTTP workflow for `POST /case-intake/route`
- human approval workflow
- ERP write-back workflow
- validation HTTP workflow, when a modernization candidate needs parity checks
- memory recording workflow

## Selector Records

Selectors should be recorded as implementation evidence, not invented silently.
Each selector note should include:

- screen URL
- UI element purpose
- selector string or stable HTML ID
- fallback strategy
- screenshot reference, when available
- author and review timestamp

## HTTP Request Variables

HTTP activities should use explicit variables:

- `routeRequestJson`
- `routeResponseJson`
- `businessRemarksText`
- `agentReasoningSummary`
- `companyContextReference`
- `llmValidationProof`
- `recommendedErpAction`
- `finalRoute`
- `policyDecision`

Do not rely on positional JSON parsing. Deserialize by field name.

## Exception Handling

Every HTTP call should handle:

- timeout
- non-2xx response
- invalid JSON
- missing required field
- `unknown_exception`
- failed validation

Failure routes should keep execution in RPA/manual mode unless governance
approves another path.

## Human Approval

Human approval records should include:

- approver ID
- decision
- reason
- timestamp
- source case ID
- PO summary
- business remarks
- agent reasoning summary
- company context snapshot or reference
- before and after ERP status

## Boundary

This skill can help draft implementation assets and review workflow structure.
It does not mean the system can automatically generate, approve, publish, or run
new XAML at runtime.
