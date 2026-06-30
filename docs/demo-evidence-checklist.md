# Demo Evidence Checklist

Capture these screenshots or short video clips for the current website-clickable
demo.

- [ ] GitHub repo: `https://github.com/wanghaowei06-ui/agentic-erp-modernization-layer`
- [ ] README project overview and UiPath component list.
- [ ] ERP Work Queue at `http://localhost:8002/erp/work-queue`.
- [ ] ERP detail page with Business Remarks visible and Technical Audit collapsed.
- [ ] Agent context trace at `http://localhost:8002/demo/agent-context-trace`.
- [ ] `/company-context` showing finance policy, sales context, and operations context.
- [ ] Route response for PO-1001 showing `agent_context_used=true`, `llm_validation_proof`, `company_context_reference`, and `recommended_erp_action`.
- [ ] Route response for PO-1000 showing deterministic precheck and no LLM invocation.
- [ ] Approval Inbox at `http://localhost:8002/approvals/inbox` with business remarks and agent reasoning.
- [ ] Case Dashboard with ERP order fields, company context used, agent decision, policy gate, UiPath action, memory commit, and pattern update.
- [ ] Simulation Dashboard at `http://localhost:8002/simulation/dashboard` showing observed count / threshold and Pattern Memory.
- [ ] Proposal Inbox at `http://localhost:8002/proposals/inbox` showing API and XAML proposals created from accumulated memory evidence.
- [ ] Codex session page after a human proposal approval.
- [ ] Evidence pack output from `./scripts/collect-demo-evidence.sh`.

The core narrative is that UiPath performs RPA extraction and ERP action while
the LangGraph route agent uses enterprise context and business remarks to choose
the next step. Modernization proposals come from repeated Run Memory reaching
threshold, not from a manual "generate proposal" button.
