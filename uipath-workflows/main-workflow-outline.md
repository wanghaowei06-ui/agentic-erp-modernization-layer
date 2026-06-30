# Main Workflow Outline

This outline describes the current `AgenticErpMvpRpa/Main.xaml` behavior at a
review level. The checked-in XAML is the source of truth.

## 1. Start Worker Loop

1. Send robot heartbeat to `http://localhost:8002/robot/heartbeat`.
2. Check `/approvals/approved-pending-writeback` for approved work that needs
   ERP write-back.
3. Open `http://localhost:8002/erp/work-queue`.
4. Claim or open a pending simulation work item.

## 2. Extract ERP Order Fields

Use stable selectors on the ERP detail page:

- `ctl00_MainContent_lblCaseId`
- `ctl00_MainContent_lblPoNumber`
- `ctl00_MainContent_lblAmount`
- `ctl00_MainContent_lblBudgetLimit`
- `ctl00_MainContent_lblVendorId`
- `ctl00_MainContent_lblExceptionReason`
- `ctl00_MainContent_lblBusinessRemarks`
- `ctl00_MainContent_lblErpStatus`

Technical audit selectors such as simulation case ID, scenario, run ID, final
route, policy decision, and last action remain available for UiPath
compatibility but are not the primary business fields.

## 3. Start Run Memory

Call:

```text
POST http://localhost:8002/memory/runs/start
```

Record raw ERP extracted fields, including `business_remarks`.

## 4. Route With Agent Context

Call:

```text
POST http://localhost:8002/case-intake/route
```

The request should include:

- ERP order fields
- `raw_exception_text`
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

`recommended_erp_action` is additive and future-compatible. The existing UiPath
branch logic still routes by `final_route` / `policy_decision`.

## 5. Branching

| Route | UiPath action |
| --- | --- |
| `STANDARD_PROCESSING` | Click `ctl00_MainContent_btnMarkStandardProcessed`. |
| `WAITING_VENDOR_INFO` | Click `ctl00_MainContent_btnMarkWaitingVendor`. |
| `CAPABILITY_GAP_DETECTED` | Click `ctl00_MainContent_btnFlagCapabilityGap`. |
| `WAITING_MANUAL_INVESTIGATION` | Click `ctl00_MainContent_btnSendManualInvestigation`. |
| `WAITING_FOR_HUMAN_APPROVAL` | Create a web approval task through `/approvals/create`; do not click ERP approval submit. |

## 6. Human Approval

Approval tasks should include:

- order summary
- business remarks
- agent reasoning summary
- company context reference or snapshot
- policy gate reason

Review them at:

```text
http://localhost:8002/approvals/inbox
```

## 7. Commit Memory

Complete and commit the run through:

```text
POST http://localhost:8002/memory/runs/{run_id}/complete
POST http://localhost:8002/memory/runs/{run_id}/commit
```

Run commit updates Pattern Memory. Proposal creation is threshold-based and
comes from repeated committed patterns, not from a button in the ERP page.

## 8. Evidence Pages

Open these pages during review:

- `http://localhost:8002/demo/agent-context-trace`
- `http://localhost:8002/case-dashboard/CASE-DEMO-AGENT-CONTEXT?run_id=RUN-DEMO-AGENT-CONTEXT-001`
- `http://localhost:8002/simulation/dashboard`
- `http://localhost:8002/approvals/inbox`
- `http://localhost:8002/proposals/inbox`
- `http://localhost:8002/codex/sessions/CODEX-PROP-API-DEMO-0001-001`
