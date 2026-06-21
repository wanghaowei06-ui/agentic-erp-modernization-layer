# Risk And Limitations

- The ERP is a mock ERP, not a real ERP system.
- Triage is deterministic in the MVP, not a general LLM reasoning engine.
- The API facade directly uses a demo data layer.
- The UiPath workflow must be configured by a human builder in UiPath Studio / Automation Cloud.
- No production ERP code is modified.
- There is no automatic Codex runtime deployment.
- Codex is not triggered by UiPath at runtime in this demo.
- The parity check is demo-grade simulation.
- Real enterprise integration would require security, governance, connector, audit, tenant, RBAC, network, and data-access hardening.
