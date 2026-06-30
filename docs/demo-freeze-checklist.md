# Demo Freeze Checklist

Use this checklist before recording or submitting the current RPA-first ERP
Worker demo.

## Service Health

- [ ] `GET http://localhost:8001/health` returns `ok`.
- [ ] `GET http://localhost:8002/health` returns `ok`.
- [ ] `GET http://localhost:8003/health` returns `ok`.
- [ ] `GET http://localhost:8004/health` returns `ok`.

## Main Flow

- [ ] `http://localhost:8002/erp/work-queue` opens as the first visible ERP screen.
- [ ] ERP detail pages show PO Number, Amount, Budget Limit, Vendor ID, ERP Status, System Message, and Business Remarks first.
- [ ] Technical selectors and demo metadata remain in the Technical Audit / RPA Metadata area.
- [ ] `POST /case-intake/route` accepts `business_remarks` and `agent_context_policy`.
- [ ] PO-1000 returns `agent_required=false` and `precheck_decision_source=deterministic_rule`.
- [ ] PO-1001 returns `agent_context_used=true`, company context proof, `llm_validation_proof`, and `recommended_erp_action=CREATE_WEB_APPROVAL_TASK`.
- [ ] PO-1002 routes to `ctl00_MainContent_btnMarkWaitingVendor`.
- [ ] PO-1003 routes to `ctl00_MainContent_btnFlagCapabilityGap`.
- [ ] PO-1004 routes to `ctl00_MainContent_btnSendManualInvestigation`.

## Agent And Context

- [ ] `/company-context` returns finance policy, sales context, operations context, and strategic goals.
- [ ] `/demo/agent-context-trace` shows UiPath extraction, enterprise context lookup, agent reasoning, policy gate, ERP action, and memory commit.
- [ ] Case dashboards show ERP fields, Business Remarks, Company Context Used, Agent Decision, Policy Gate, UiPath Action, Memory Commit, and Pattern Update.
- [ ] Deterministic cases are not presented as LLM-backed decisions.

## Run And Pattern Memory

- [ ] Run Memory records ERP extracted fields, `business_remarks`, route plan, policy gate, selected ERP action, and final branch result.
- [ ] Pattern Memory groups by business action, exception type, route family, policy gate family, and side-effect signature.
- [ ] `/simulation/dashboard` shows observed count / threshold, business remarks examples, company context used, and agent analysis.
- [ ] Proposal rows appear only after repeated UiPath-processed runs reach the configured threshold.
- [ ] Threshold is `3` for the recording path unless `PROPOSAL_THRESHOLD` is intentionally changed.

## Proposals And Codex

- [ ] `/proposals/inbox` shows both `API_MODERNIZATION_PROPOSAL` and `XAML_WORKFLOW_PROPOSAL` when seeded demo memory is loaded.
- [ ] Proposal approval is a human click.
- [ ] Before approval, no Codex CLI is started automatically.
- [ ] After approval, the Codex session page shows either mock staged progress or real CLI mode, depending on `CODEX_CLI_DEMO_MODE`.
- [ ] No endpoint auto-deploys an API, auto-registers a trusted capability, auto-approves a proposal, or auto-modifies Windows XAML.

## Evidence Pack

- [ ] `./scripts/collect-demo-evidence.sh` runs successfully.
- [ ] `docs/evidence/` contains health checks, company context, five route responses, approval-task proof, proposal inbox, simulation state, and demo evidence snapshot.
- [ ] `docs/evidence/manifest.json` exists and lists the current evidence files.
- [ ] Evidence files do not depend on the legacy `/triage` endpoint.

## UiPath

- [ ] Current UiPath project opens from `uipath-workflows/AgenticErpMvpRpa/project.json`.
- [ ] `Main.xaml` is the current RPA-first ERP Worker.
- [ ] `RouteProof_PO1002.xaml` and `RouteProof_PO1003.xaml` are included only because the UiPath project references them.
- [ ] Existing Windows XAML logic is not manually edited in this cleanup.
