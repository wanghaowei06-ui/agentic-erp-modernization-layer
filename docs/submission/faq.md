# FAQ

## Why not direct API modernization?

Because the starting point may be a legacy UI with no safe public business API. The MVP shows how UiPath can govern the transition from RPA evidence to a validated API candidate.

## Why RPA first?

RPA captures the current behavior safely through the UI and gives the modernization process evidence before API mode is trusted.

## Why UiPath as orchestrator?

UiPath owns robots, approvals, case lifecycle, governance, validation gates, and execution-mode decisions. The Python services are support assets only.

## What does the agent do?

The triage support service classifies exception fields and returns a structured route recommendation. In the MVP it is deterministic for reliability.

## What does Codex do?

Codex generated support services, fixtures, docs, scripts, and implementation aids. Codex does not run at UiPath runtime in this demo.

## Is this production ready?

No. It is a hackathon MVP and local demo.

## What is validated?

The validation suite simulates contract checks, business rule checks, and RPA/API parity checks for `request_purchase_order_approval`.

## What does parity mean?

Parity means the RPA path and API candidate are compared on cloned/reset test cases for key business fields like status, audit log creation, and last action.

## How do humans stay in control?

UiPath pauses for human approval on high-risk routes and trusted-tool registration. If validation fails, API mode is not used.

## What is the difference between RPA mode and API mode?

RPA mode interacts with the legacy ERP through Chrome UI. API mode calls the validated API facade candidate after UiPath governance approves it.
