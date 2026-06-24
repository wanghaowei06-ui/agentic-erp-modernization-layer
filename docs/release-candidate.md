# Release Candidate

## Version

Recommended release candidate name:

```text
hard-mvp-automation-memory-rc1
```

## Included Capabilities

- Deterministic structured triage for PO exceptions.
- UiPath orchestration compatibility through stable HTTP contracts.
- Mock Legacy ERP RPA write-back trace.
- Validation parity for the `request_purchase_order_approval` action.
- generated-api-facade API execution with `execution_mode=API`.
- Automation Memory timeline using append-only JSONL events.
- Capability registry with trusted API and human task capabilities.
- Capability gap record for uncovered inventory shortage workflow.
- Evidence pack generation under `docs/evidence/`.

## Explicitly Not Included

- LLM structured triage.
- Multi-agent debate or autonomous agent routing.
- Vector memory as an audit source.
- Graph memory as an audit source.
- PostgreSQL or enterprise database backend.
- Automatic UiPath XAML generation.
- Automatic production trusted capability registration.
- Full enterprise RBAC, tenant isolation, or production security controls.

## API Capability / XAML Modernization Trigger Policy

RC1 does not automatically trigger API generation or UiPath XAML modernization. It freezes the governance foundation: Automation Memory, validation parity, capability registry evidence, and capability gap recording.

Future API capability promotion should be triggered only by fixed readiness gates, RPA/API validation parity, and human approval. Future XAML modernization should be triggered only after a trusted capability is identified, a human owner confirms the change, Codex prepares a draft PR or implementation brief, UiPath tests pass, and the validation gate remains green.

LLMs may generate proposals, implementation briefs, or draft PR context in Enhanced Mode. They do not approve production deployment, register trusted capabilities, or decide that XAML/API changes go live.

## Recommended Demo Path

Use CASE-001 / PO-1001 for the main Hard MVP:

1. UiPath reads PO-1001 from Mock Legacy ERP.
2. reasoning-agent returns `budget_exceeded`.
3. UiPath routes to human approval.
4. UiPath performs RPA write-back.
5. validation-suite returns parity passed.
6. generated-api-facade executes the API path.
7. Memory timeline shows triage, RPA write-back, validation, and API execution events.

Use CASE-003 / PO-1003 for route proof:

1. reasoning-agent returns `inventory_shortage`.
2. The system records a capability gap.
3. Demo shows the gap through `GET http://localhost:8004/memory/gaps`.

## Rollback Strategy

If an Enhanced Mode experiment fails, roll back to `hard-mvp-automation-memory-rc1`.

The Hard MVP does not depend on an LLM API key, vector database, graph database, or external paid service. The support services can be restarted with:

```bash
./scripts/dev-stop.sh
./scripts/dev-start.sh
```

Rebuild the evidence baseline with:

```bash
make demo-check
```
