# Coding Agent Prompts

## Hard MVP Backend Prompt Summary

Codex was asked to own only the Linux, backend, test, and documentation parts of `agentic-erp-modernization-layer`.

Key boundaries:

- Do not edit UiPath XAML.
- Do not handle Windows selectors.
- Keep the Mock Legacy ERP endpoint and page structure compatible.
- Provide stable HTTP contracts that Windows UiPath can call.
- Use deterministic triage rules for the Hard MVP instead of requiring a live LLM.
- Use cloned parity data, `PO-1001-RPA` and `PO-1001-API`, rather than comparing mutated production demo records.
- Treat generated API facade and LangChain wrapper as support assets, not the main orchestrator.
- Treat Structured Automation Memory JSON artifacts as the audit source for the demo.

Prompted deliverables:

- Service ports: Mock ERP `8001`, reasoning-agent `8002`, generated API facade `8003`, validation-suite `8004`.
- `GET /health` on every service.
- `POST /triage` with stable `detected_exception_type` output.
- `POST /api/purchase-orders/{po_id}/approval-request` returning `execution_mode=API`.
- `POST /validate/request-purchase-order-approval` returning contract, rule, and parity results.
- Memory artifacts under `memory/data/`.
- `scripts/dev-start.sh`, `scripts/dev-stop.sh`, and `scripts/smoke-test.sh`.
