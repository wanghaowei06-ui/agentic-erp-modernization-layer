# Release Candidate

## Version

Recommended release candidate name:

```text
rpa-first-erp-worker-enterprise-context-rc2
```

## Included Capabilities

- UiPath-first ERP work queue and order detail pages on `http://localhost:8002/erp/work-queue`.
- Stable WebForms-style selectors for UiPath, including `ctl00_MainContent_lblBusinessRemarks`.
- `POST /case-intake/route` as the current route-agent contract.
- `GET /company-context` mock enterprise context with finance, sales, operations, and strategic-goal signals.
- LangGraph route-agent path for agent-required cases.
- Deterministic precheck for normal cases, clearly marked as non-LLM.
- Recommended ERP action mapping for legacy UiPath branching and future-compatible evidence.
- Human approval inbox with business remarks, agent reasoning, company context snapshot, and policy gate reason.
- Run Memory, Pattern Memory, threshold-based proposal creation, and proposal inbox.
- Human-approved Codex CLI handoff with mock and real execution modes.
- Evidence pack generation under `docs/evidence/`.

## Explicitly Not Included

- Automatic proposal approval.
- Automatic Codex call before a human approves a proposal.
- Automatic production API deployment.
- Automatic trusted capability registration.
- Automatic Windows XAML modification.
- Production ERP integration, production tenant isolation, or enterprise RBAC.

## Modernization Trigger Policy

Modernization proposals are not button-triggered. They are created when repeated
UiPath-processed runs are committed to Run Memory, aggregated into Pattern
Memory, and the observed count reaches the configured threshold.

The default proposal threshold is `3`. The demo uses:

- repeated CAPEX budget exceptions for `API_MODERNIZATION_PROPOSAL`
- repeated inventory shortages for `XAML_WORKFLOW_PROPOSAL`

All proposal follow-up remains governed. A human must approve before Codex CLI
handoff starts, and even then no API deployment, trusted capability
registration, or Windows XAML modification is automatic.

## Recommended Demo Path

1. Open the ERP Work Queue.
2. Open a PO detail page and show business-facing fields plus hidden technical metadata.
3. Show `/demo/agent-context-trace`.
4. Show PO-1001 route evidence with company context and `llm_validation_proof`.
5. Show PO-1000 deterministic precheck evidence.
6. Show `/approvals/inbox`.
7. Show `/simulation/dashboard` with observed count / threshold.
8. Show `/proposals/inbox`.
9. Human-approve a proposal and show the Codex session page.

## Rollback Strategy

The demo does not require real LLM mode or real Codex CLI mode. For recording,
use mock modes if external services are unavailable:

```bash
LLM_DEMO_MODE=mock_success
CODEX_CLI_DEMO_MODE=mock
```

Restart the support services with:

```bash
./scripts/dev-stop.sh
./scripts/dev-start.sh
```
