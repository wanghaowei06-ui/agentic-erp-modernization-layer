# UiPath Authoring Skill

This document defines future implementation assistance for drafting UiPath workflow assets from a business action. It is not a runtime auto-deployment feature.

## Input Parameters

A future authoring request should include:

- `business_action`
- `case_id`
- `po_id`
- source application name and URL
- expected input fields
- expected output fields
- required human approval stage
- validation endpoint
- API endpoint, if one exists
- exception handling requirements
- selector evidence collected by a human or UiPath author

## XAML Split

Generated drafts should separate concerns:

- `Main.xaml` for high-level orchestration handoff points
- extraction workflow for reading legacy ERP fields
- triage HTTP workflow
- human approval workflow
- write-back workflow
- validation HTTP workflow
- API-mode execution workflow
- memory recording workflow

## Selector Records

Selectors should be recorded as implementation evidence, not invented silently. Each selector note should include:

- screen URL
- UI element purpose
- selector string or stable HTML ID
- fallback strategy
- screenshot reference, when available
- author and review timestamp

## HTTP Request Variables

HTTP activities should use explicit variables:

- `triageRequestJson`
- `triageResponseJson`
- `detectedExceptionType`
- `nextStage`
- `validationRequestJson`
- `validationResponseJson`
- `apiRequestJson`
- `apiResponseJson`
- `executionMode`

Do not rely on positional JSON parsing. Deserialize by field name.

## Exception Handling

Every HTTP call should handle:

- timeout
- non-2xx response
- invalid JSON
- missing required field
- `unknown_exception`
- failed validation

Failure routes should keep execution in RPA/manual mode unless governance approves another path.

## Human Approval

Human approval records should include:

- approver ID
- decision
- reason
- timestamp
- source case ID
- before and after ERP status

Human approval is mandatory for the PO-1001 budget-exceeded Hard MVP route.

## Boundary

This skill can help draft implementation assets and review workflow structure. It does not mean the system can automatically generate, approve, publish, or run new XAML at runtime.
