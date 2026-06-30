# UiPath Implementation Guide

UiPath is the RPA-first orchestration layer. The current checked-in project is:

```text
uipath-workflows/AgenticErpMvpRpa/project.json
```

The main workflow is:

```text
Main.xaml
```

## Current UiPath-Facing Endpoint

UiPath should open:

```text
http://localhost:8002/erp/work-queue
```

This page preserves stable WebForms-style selectors and presents a realistic ERP
order-processing view.

## Current Route Endpoint

UiPath should call:

```text
POST http://localhost:8002/case-intake/route
```

The request should include:

- PO number
- amount
- budget limit
- vendor ID
- vendor and inventory flags
- ERP status
- system exception reason
- `business_remarks`
- `agent_context_policy=fetch_enterprise_context_before_decision`

The response includes:

- `final_route`
- `policy_decision`
- `policy_gate`
- `agent_context_used`
- `company_context_reference`
- `agent_reasoning_summary`
- `llm_validation_proof`
- `recommended_erp_action`

## Branch Mapping

| `final_route` | UiPath behavior |
| --- | --- |
| `STANDARD_PROCESSING` | Click `ctl00_MainContent_btnMarkStandardProcessed`. |
| `WAITING_VENDOR_INFO` | Click `ctl00_MainContent_btnMarkWaitingVendor`. |
| `CAPABILITY_GAP_DETECTED` | Click `ctl00_MainContent_btnFlagCapabilityGap`. |
| `WAITING_MANUAL_INVESTIGATION` | Click `ctl00_MainContent_btnSendManualInvestigation`. |
| `WAITING_FOR_HUMAN_APPROVAL` | Create `/approvals/create`; do not click ERP approval submit. |

`recommended_erp_action` is included as proof and future compatibility. Existing
UiPath branch logic can continue to use `final_route` and `policy_decision`.

## Stable Selectors

See:

```text
uipath-workflows/selectors/mock-erp-element-ids.md
```

The important detail selector added for the enterprise scenario is:

```text
ctl00_MainContent_lblBusinessRemarks
```

## Human Approval

Approval tasks should include:

- PO summary
- amount and budget
- system message
- business remarks
- agent recommendation
- company context snapshot/reference
- policy gate reason

Review tasks at:

```text
http://localhost:8002/approvals/inbox
```

## Memory And Proposals

`Main.xaml` writes Run Memory and commits Pattern Memory. Proposal creation is
threshold-based. Do not add a manual "create proposal" button to UiPath.

Review:

```text
http://localhost:8002/simulation/dashboard
http://localhost:8002/proposals/inbox
```

After a human approves a proposal, the Codex handoff page shows either a mock
stream or real local Codex CLI mode depending on environment settings.

## Legacy Compatibility

`POST /triage` and the older `mock-legacy-erp` PO pages remain in the repo for
compatibility, older route proof workflows, validation examples, and tests. The
current RPA-first ERP Worker demo uses `/erp/work-queue` and
`/case-intake/route`.
