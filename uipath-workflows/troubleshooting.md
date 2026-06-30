# Troubleshooting

## Windows Cannot Access WSL Localhost

- Confirm services are running with `./scripts/dev-start.sh`.
- Open `http://localhost:8002/health` in Windows Chrome.
- If localhost forwarding is unavailable, get the WSL IP with `hostname -I` and
  use `http://<wsl-ip>:8002`.

## UiPath Cannot Open The Work Queue

- Open `http://localhost:8002/erp/work-queue` manually in Chrome.
- Confirm the reasoning-agent service is running on port `8002`.
- Confirm selectors in `selectors/mock-erp-element-ids.md`.
- Re-pick elements by stable `id` if the browser extension lost its target.

## Route Agent Call Fails

- Current endpoint: `POST http://localhost:8002/case-intake/route`.
- Set `Content-Type` to `application/json`.
- Include `business_remarks` and
  `agent_context_policy=fetch_enterprise_context_before_decision` for
  agent-required cases.
- Check `http-request-bodies/case-intake-route-po-1001.json` for a known-good
  body.

## JSON Parse Fails

- Log the raw route response before parsing.
- Use `final_route` and `policy_decision` for current branch logic.
- Use `recommended_erp_action` as additive evidence; do not make old workflows
  depend on it until intentionally updated.

## Human Approval Is Expected But ERP Button Is Not Clicked

That is current behavior for `WAITING_FOR_HUMAN_APPROVAL`. UiPath should create
a web approval task through `/approvals/create` and show it in:

```text
http://localhost:8002/approvals/inbox
```

## Proposals Do Not Appear

Proposals are not created by clicking ERP action buttons. They appear only when
committed Run Memory updates Pattern Memory and the observed count reaches the
threshold. Check:

```text
http://localhost:8002/simulation/dashboard
http://localhost:8002/proposals/inbox
```

Default threshold is `3`, configurable through `PROPOSAL_THRESHOLD`.

## Codex Session Looks Like A Mock Stream

That is controlled by environment:

- `CODEX_CLI_DEMO_MODE=mock` or `CODEX_CLI_EXECUTION_MODE=mock` shows a staged
  demo stream.
- `CODEX_CLI_EXECUTION_MODE=real` attempts real local `codex exec` after human
  proposal approval.

## Project Opens With Missing XAML

The repository should include every entry point referenced by
`AgenticErpMvpRpa/project.json`:

- `Main.xaml`
- `RouteProof_PO1002.xaml`
- `RouteProof_PO1003.xaml`

If UiPath Studio reports another missing file, check whether `project.json` was
changed locally in Windows and copy the referenced workflow intentionally.
