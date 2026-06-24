# Demo Freeze Checklist

Use this checklist before recording or submitting the Hard MVP demo. The freeze target is `hard-mvp-automation-memory-rc1`.

## Service Health

- [ ] `GET http://localhost:8001/health` returns `ok`.
- [ ] `GET http://localhost:8002/health` returns `ok`.
- [ ] `GET http://localhost:8003/health` returns `ok`.
- [ ] `GET http://localhost:8004/health` returns `ok`.

## Main Flow

- [ ] PO-1001 triage returns `detected_exception_type=budget_exceeded`.
- [ ] Human approval route is explainable as manager approval for budget exception.
- [ ] RPA write-back changes PO status to `PENDING_MANAGER_APPROVAL`.
- [ ] validation-suite returns `rpa_api_parity_check=passed`.
- [ ] generated-api-facade approval request returns `execution_mode=API`.

## Automation Memory

- [ ] CASE-001 timeline contains `TRIAGE_COMPLETED`.
- [ ] CASE-001 timeline contains `RPA_WRITEBACK_COMPLETED`.
- [ ] CASE-001 timeline contains `VALIDATION_COMPLETED`.
- [ ] CASE-001 timeline contains `API_EXECUTION_COMPLETED`.
- [ ] `/memory/capabilities` returns trusted API capability `cap_api_request_po_approval_v1`.
- [ ] `/memory/capabilities` returns human task capability `cap_human_po_approval_v1`.
- [ ] `/memory/gaps` returns CASE-003 / PO-1003 inventory shortage capability gap evidence.

## Evidence Pack

- [ ] `./scripts/collect-demo-evidence.sh` runs successfully.
- [ ] `docs/evidence/` contains health JSON files.
- [ ] `docs/evidence/` contains triage, write-back, validation, generated API, timeline, capabilities, and gaps evidence.
- [ ] `docs/evidence/manifest.json` exists.
- [ ] Evidence files use clear names and current ports.
- [ ] Evidence files do not reference obsolete endpoint paths.

## Documentation

- [ ] README uses ports `8001`, `8002`, `8003`, and `8004`.
- [ ] `docs/demo-script.md` matches the implemented endpoints.
- [ ] `docs/runbook.md` commands are executable.
- [ ] `docs/judging-narrative.md` does not overstate LLM capability.
- [ ] Enhanced Mode is described as future optional capability, not a Hard MVP dependency.

## UiPath

- [ ] Current Hard MVP XAML does not need modification.
- [ ] Legacy best-effort XAML references, including any old `localhost:8000` text, are not used for the current demo.
- [ ] Current demo should use Main.xaml / Hard MVP flow assets configured for ports `8001` through `8004`.

## Canonical Demo Cases

- CASE-001 / PO-1001 is the main Hard MVP path.
- CASE-003 / PO-1003 is the capability gap path.
- `API_MODE_EXECUTED` is a UiPath stage label.
- `API_EXECUTION_COMPLETED` is the Automation Memory event emitted by generated-api-facade.
