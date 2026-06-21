# Docker Compose Run Guide

Docker Compose is optional for this demo. The WSL2 virtualenv flow remains the primary local setup for UiPath on Windows.

Use Docker Compose when reviewers want to start the four Python support services quickly:

```bash
docker compose up --build
```

Services:

- `http://localhost:8000` mock legacy ERP UI and demo evidence pages
- `http://localhost:8001` triage support service
- `http://localhost:8002` generated API facade candidate
- `http://localhost:8003` validation support service

Run smoke tests from another terminal:

```bash
./scripts/smoke_test.sh
```

Stop services:

```bash
docker compose down
```

Docker does not add orchestration. UiPath remains the case orchestration, governance, approval, RPA, validation, trusted-tool registration, and API-mode execution layer.
