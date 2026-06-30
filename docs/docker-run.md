# Docker Compose Run Guide

Docker Compose is optional. The WSL2 virtualenv flow remains the primary local
setup when UiPath Studio and the Windows robot are part of the demo.

Use Docker Compose when reviewers want to start the Python support services
quickly:

```bash
docker compose up --build
```

Services:

- `http://localhost:8001` mock legacy ERP support service.
- `http://localhost:8002` RPA-first ERP Worker backend, LangGraph route agent, ERP work queue, approvals, simulation dashboard, and proposal inbox.
- `http://localhost:8003` generated API facade candidate.
- `http://localhost:8004` validation support service.

Run smoke tests from another terminal:

```bash
./scripts/smoke_test.sh
```

Stop services:

```bash
docker compose down
```

Docker does not replace UiPath orchestration. UiPath remains responsible for RPA
execution and selector-driven ERP interaction. The backend provides route
decisions, enterprise context, memory evidence, approval tasks, and governed
proposal handoff.
