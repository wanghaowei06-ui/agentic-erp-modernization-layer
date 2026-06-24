# Risk And Limitations

- The ERP is a mock ERP, not a real ERP system.
- The Hard MVP reasoning service uses deterministic structured triage for stable governance. LLM structured triage is an optional Enhanced Mode path, not a runtime dependency.
- The API facade directly uses a demo data layer.
- The UiPath workflow must be configured by a human builder in UiPath Studio / Automation Cloud.
- No production ERP code is modified.
- There is no automatic Codex runtime deployment.
- Codex is not triggered by UiPath at runtime in this demo.
- The parity check is demo-grade simulation.
- Automation Memory currently uses a JSON/JSONL repository adapter, not a production database.
- The project does not automatically generate, deploy, or approve UiPath XAML.
- Real enterprise integration would require security, governance, connector, audit, tenant, RBAC, network, and data-access hardening.
