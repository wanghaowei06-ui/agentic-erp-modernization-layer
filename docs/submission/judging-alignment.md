# Judging Alignment

| Criterion | Project alignment |
| --- | --- |
| Dynamic exception-heavy workflow | Five PO cases cover normal, budget, vendor, inventory, and ambiguous routes. |
| UiPath + agent integration | UiPath calls `/case-intake/route`; the agent returns route, policy gate, reasoning, context proof, and ERP action. |
| Human-in-the-loop | Budget approvals and modernization proposals require human approval. |
| Enterprise context | Agent-required cases fetch finance, sales, and operations context before deciding. |
| RPA-first | UiPath opens the ERP queue and uses stable selectors; no API replacement is assumed upfront. |
| Memory-driven modernization | Run Memory and Pattern Memory accumulate evidence before proposals are created. |
| Governance | No auto-approval, auto-deploy, trusted registration, or automatic XAML modification. |
| Reviewable evidence | Dashboard, case dashboard, approvals inbox, proposal inbox, and Codex session pages are clickable. |
