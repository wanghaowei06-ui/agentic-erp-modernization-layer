# Risk And Limitations

- The ERP is a local mock, not a production ERP.
- The enterprise context is a local mock snapshot.
- Real LLM mode requires external credentials.
- Codex real mode requires local Codex CLI setup and explicit configuration.
- UiPath project files are included for review, but Windows runtime caches and
  local Studio folders are intentionally excluded.
- Modernization proposals are not deployments.
- The project does not automatically approve, merge, deploy APIs, register
  trusted capabilities, or modify Windows XAML.
- Security, RBAC, tenant isolation, real ERP connectors, and production audit
  hardening would be required for enterprise use.
