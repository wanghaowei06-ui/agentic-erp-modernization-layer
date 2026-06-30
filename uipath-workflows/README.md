# UiPath Workflow Pack

This folder contains the UiPath assets for the current RPA-first ERP Worker
demo.

## Current Project

Open this project in UiPath Studio:

```text
uipath-workflows/AgenticErpMvpRpa/project.json
```

Included project files:

- `AgenticErpMvpRpa/Main.xaml`
- `AgenticErpMvpRpa/RouteProof_PO1002.xaml`
- `AgenticErpMvpRpa/RouteProof_PO1003.xaml`
- `AgenticErpMvpRpa/project.json`
- `AgenticErpMvpRpa/entry-points.json`

`Main.xaml` is the main RPA-first ERP Worker. It opens the ERP work queue,
extracts purchase-order fields, calls the route agent, records Run Memory, and
clicks the appropriate ERP action or creates a human approval task.

The `RouteProof_*` files are included because they are referenced by
`project.json` and `entry-points.json`. They are copied from the Windows UiPath
project as-is so the project can open cleanly.

## Current Runtime Flow

UiPath remains the case orchestration and execution governance layer:

1. Open `http://localhost:8002/erp/work-queue`.
2. Extract ERP fields from stable WebForms-style selectors.
3. Call `POST http://localhost:8002/case-intake/route`.
4. Use `final_route`, `policy_decision`, and the legacy-compatible route fields
   to branch.
5. Use `recommended_erp_action` as display evidence and future compatibility.
6. Create `/approvals/create` tasks for human approval routes.
7. Write Run Memory artifacts and commit Pattern Memory.
8. Let Pattern Memory generate proposals only when repeated observations reach
   the configured threshold.

## Stable Selector Contract

The ERP pages retain UiPath selector IDs, including:

- `ctl00_MainContent_btnOpenFirstPending`
- `ctl00_MainContent_lblQueueEmptyMessage`
- `ctl00_MainContent_grdPoWorkQueue`
- `ctl00_MainContent_lblPoNumber`
- `ctl00_MainContent_lblAmount`
- `ctl00_MainContent_lblBudgetLimit`
- `ctl00_MainContent_lblVendorId`
- `ctl00_MainContent_lblExceptionReason`
- `ctl00_MainContent_lblBusinessRemarks`
- `ctl00_MainContent_btnMarkStandardProcessed`
- `ctl00_MainContent_btnMarkWaitingVendor`
- `ctl00_MainContent_btnFlagCapabilityGap`
- `ctl00_MainContent_btnSendManualInvestigation`

Technical/debug fields are still present for selector compatibility, but the
page presents a business-facing ERP order view.

## Current Samples

- Request bodies: `http-request-bodies/case-intake-route-po-*.json`
- Expected route outputs: `expected-outputs/case-intake-route-po-*-response.json`
- Selector notes: `selectors/mock-erp-element-ids.md`
- Demo runbook: `demo-runbook.md`

The older `/triage` endpoint remains in the backend for compatibility and unit
tests, but it is not the current UiPath demo entry point. The current entry
point is `/case-intake/route`.

## Safety Boundary

UiPath action buttons and ERP pages do not create modernization proposals or
call Codex by themselves. Proposals are generated only from committed Run
Memory and Pattern Memory after threshold evidence. Codex handoff starts only
after a human approves a proposal in `/proposals/inbox`.
