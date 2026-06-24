# Codex Draft PR Prompt

Create a draft PR for the candidate tool `request_purchase_order_approval`.

Scope:

- Keep UiPath as the orchestration and governance layer.
- Do not implement case orchestration in Python.
- Do not auto-merge the PR.
- Do not claim production ERP modernization.

Inputs:

- `generated-artifacts/request_purchase_order_approval/modernization-plan.json`
- `generated-artifacts/request_purchase_order_approval/openapi-candidate.json`
- `generated-artifacts/request_purchase_order_approval/contract-test.md`
- `generated-artifacts/request_purchase_order_approval/parity-test.md`

Expected PR content:

- Explain the candidate API facade contract.
- Reference validation-suite requirements.
- State that UiPath must approve trusted-tool registration before API mode.
- Include no claims that tests have passed unless validation-suite output is attached separately.
