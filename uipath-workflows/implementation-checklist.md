# Implementation Checklist

Use this checklist when opening or reviewing the UiPath Studio project.

- [ ] Open `uipath-workflows/AgenticErpMvpRpa/project.json`.
- [ ] Confirm `Main.xaml` is the main workflow.
- [ ] Confirm `RouteProof_PO1002.xaml` and `RouteProof_PO1003.xaml` are present
      because they are declared entry points.
- [ ] Start backend services with `./scripts/dev-start.sh`.
- [ ] Open `http://localhost:8002/erp/work-queue`.
- [ ] Confirm the queue selectors exist:
      `ctl00_MainContent_btnOpenFirstPending`,
      `ctl00_MainContent_lblQueueEmptyMessage`,
      `ctl00_MainContent_grdPoWorkQueue`.
- [ ] Confirm the detail page selectors exist, including
      `ctl00_MainContent_lblBusinessRemarks`.
- [ ] Extract ERP order fields: PO number, amount, budget limit, vendor ID,
      ERP status, exception reason, business remarks.
- [ ] Build the route request body with `business_remarks` and
      `agent_context_policy=fetch_enterprise_context_before_decision`.
- [ ] Call `POST http://localhost:8002/case-intake/route`.
- [ ] Parse `final_route`, `policy_decision`, `agent_context_used`,
      `company_context_reference`, `agent_reasoning_summary`,
      `llm_validation_proof`, and `recommended_erp_action`.
- [ ] For `WAITING_FOR_HUMAN_APPROVAL`, create `/approvals/create`; do not click
      an ERP approval submit button.
- [ ] For `WAITING_VENDOR_INFO`, click
      `ctl00_MainContent_btnMarkWaitingVendor`.
- [ ] For `CAPABILITY_GAP_DETECTED`, click
      `ctl00_MainContent_btnFlagCapabilityGap`.
- [ ] For `WAITING_MANUAL_INVESTIGATION`, click
      `ctl00_MainContent_btnSendManualInvestigation`.
- [ ] For `STANDARD_PROCESSING`, click
      `ctl00_MainContent_btnMarkStandardProcessed`.
- [ ] Write Run Memory artifacts and commit the run.
- [ ] Show `/case-dashboard/{case_id}?run_id=...`,
      `/simulation/dashboard`, `/approvals/inbox`, and `/proposals/inbox`.
- [ ] Verify proposals appear only after Pattern Memory reaches threshold.
- [ ] Verify proposal approval starts Codex handoff only after a human click.
