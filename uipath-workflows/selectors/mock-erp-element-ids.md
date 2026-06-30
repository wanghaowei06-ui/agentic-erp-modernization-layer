# ERP Work Queue And Detail Selectors

Use UiPath browser automation against the legacy-style ERP pages served by the
reasoning-agent service.

Current ERP entry point:

```text
http://localhost:8002/erp/work-queue
```

The pages intentionally keep WebForms-style IDs so existing UiPath selectors
remain stable.

## Work Queue

| Selector ID | UiPath usage |
| --- | --- |
| `ctl00_MainContent_btnOpenFirstPending` | Open the first pending queue item. |
| `ctl00_MainContent_lblQueueEmptyMessage` | Detect an empty work queue. |
| `ctl00_MainContent_grdPoWorkQueue` | Read or verify the PO work queue table. |

## Business Fields On Detail Page

| Selector ID | UiPath usage |
| --- | --- |
| `ctl00_MainContent_lblCaseId` | Get case ID. |
| `ctl00_MainContent_lblPoNumber` | Get PO number. |
| `ctl00_MainContent_lblAmount` | Get amount. |
| `ctl00_MainContent_lblBudgetLimit` | Get budget limit. |
| `ctl00_MainContent_lblVendorId` | Get vendor ID. |
| `ctl00_MainContent_lblExceptionReason` | Get system message / exception reason. |
| `ctl00_MainContent_lblBusinessRemarks` | Get buyer, manager, or operations notes. |
| `ctl00_MainContent_lblErpStatus` | Get ERP status. |
| `ctl00_MainContent_lblSimulationStatus` | Get queue item status. |

## ERP Action Buttons

| Selector ID | UiPath usage |
| --- | --- |
| `ctl00_MainContent_btnMarkStandardProcessed` | Standard processing route. |
| `ctl00_MainContent_btnMarkWaitingVendor` | Waiting for vendor information route. |
| `ctl00_MainContent_btnFlagCapabilityGap` | Capability gap route. |
| `ctl00_MainContent_btnSendManualInvestigation` | Manual investigation route. |
| `ctl00_MainContent_btnSubmitApprovalRequest` | Legacy selector retained; current human approval path should create `/approvals/create` instead of clicking this for agent-gated approval. |

## Technical Audit Selectors

These selectors are retained for RPA compatibility and audit evidence. They
should not be the first business-facing content in the demo.

| Selector ID |
| --- |
| `ctl00_MainContent_lblSimulationCaseId` |
| `ctl00_MainContent_lblScenario` |
| `ctl00_MainContent_lblRunId` |
| `ctl00_MainContent_lblFinalRoute` |
| `ctl00_MainContent_lblPolicyDecision` |
| `ctl00_MainContent_lblLastAction` |

## Selector Strategy

- Prefer selectors that include the stable HTML `id` attribute.
- Avoid row position, nearby text, or visual coordinates.
- Reopen `/erp/work-queue` before reselection.
- Do not rebind `Main.xaml` unless a human intentionally changes the workflow.
