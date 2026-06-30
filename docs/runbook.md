# Runbook

## Environment

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
chmod +x scripts/*.sh
```

For deterministic demos:

```bash
cp .env.example .env
printf '\nLLM_DEMO_MODE=mock_success\nCODEX_CLI_DEMO_MODE=mock\n' >> .env
```

## Start Services

```bash
./scripts/dev-start.sh
```

Ports:

- Mock Legacy ERP support service: `http://localhost:8001`
- Reasoning Agent / ERP Worker UI: `http://localhost:8002`
- Generated API Facade: `http://localhost:8003`
- Validation Suite: `http://localhost:8004`

Stop services:

```bash
./scripts/dev-stop.sh
```

## Current Demo URLs

- `http://localhost:8002/erp/work-queue`
- `http://localhost:8002/demo/agent-context-trace`
- `http://localhost:8002/case-dashboard/CASE-DEMO-AGENT-CONTEXT?run_id=RUN-DEMO-AGENT-CONTEXT-001`
- `http://localhost:8002/simulation/dashboard`
- `http://localhost:8002/approvals/inbox`
- `http://localhost:8002/proposals/inbox`
- `http://localhost:8002/company-context`

## UiPath Project

Open:

```text
uipath-workflows/AgenticErpMvpRpa/project.json
```

Run:

```text
Main.xaml
```

The project directory includes all entry points referenced by `project.json`:

- `Main.xaml`
- `RouteProof_PO1002.xaml`
- `RouteProof_PO1003.xaml`

## Route Agent Check

Use the current route endpoint:

```text
POST http://localhost:8002/case-intake/route
```

Sample bodies live in:

```text
uipath-workflows/http-request-bodies/case-intake-route-po-*.json
```

The legacy `/triage` endpoint is still present for compatibility tests, but it
is no longer the current UiPath main route endpoint.

## Tests

```bash
PYTHONPATH=$PWD .venv/bin/pytest reasoning-agent/tests -q
PYTHONPATH=$PWD .venv/bin/pytest reasoning-agent/tests generated-api-facade/tests validation-suite/tests shared/automation_memory/tests shared/auth/tests -q
```

Run JSON/sample validation and service tests:

```bash
./scripts/ci_test.sh
```

## Demo Reset

```bash
./scripts/reset_demo_data.sh
.venv/bin/python scripts/seed_historical_memory.py
```

## Troubleshooting

If Windows cannot open `localhost`, check WSL forwarding or use the WSL IP from:

```bash
hostname -I
```

If proposals do not appear, check `/simulation/dashboard`: proposals are created
from committed Pattern Memory after the observed-count threshold is reached, not
from an ERP button click.
