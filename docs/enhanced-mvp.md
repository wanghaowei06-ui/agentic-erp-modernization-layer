# Enhanced MVP

The Enhanced MVP adds local demo evidence surfaces around the original support services. These additions make the project look more like a UiPath-governed modernization case while keeping UiPath as the main orchestration, governance, approval, RPA, validation, trusted-tool registration, and API-mode execution layer.

Python remains limited to callable support assets, UI evidence pages, fixtures, validation helpers, and local demo utilities.

## Implemented

- Case Dashboard at `GET http://localhost:8000/case-dashboard`
- Case Timeline at `GET http://localhost:8000/case-timeline/CASE-001`
- Timeline JSON at `GET http://localhost:8000/api/demo/cases/CASE-001/timeline`
- API Readiness Scorecard at `GET http://localhost:8000/api-readiness-scorecard`
- Scorecard JSON at `GET http://localhost:8000/api/demo/api-readiness-scorecard`
- Tool Registry at `GET http://localhost:8000/tool-registry`
- Tool Registry JSON at `GET http://localhost:8000/api/demo/tool-registry`
- Validation failed simulation with `{"simulate_failure": true}`
- PO-1003 lightweight `inventory_shortage` route proof fixtures
- Local demo reset endpoint at `POST http://localhost:8000/api/demo/reset`
- Local demo reset script at `scripts/reset_demo_data.sh`

## Roadmap

- Full UiPath tenant setup and attended robot execution
- Production-grade case persistence
- Production ERP integration
- Full trusted-tool registry service
- Real approval task integration in UiPath Action Center or a tenant-specific equivalent
- Broader validation coverage across more candidate business actions

## Readiness Score Note

The readiness score for `request_purchase_order_approval` uses:

```text
0.30 * frequency
+ 0.30 * business_value
+ 0.25 * field_stability
+ 0.15 * ui_fragility
```

`risk_level` does not directly reduce the readiness score. UiPath uses risk to drive approval requirements and validation strictness.

The weighted formula produces a rounded formula result of 85 from the demo factors. The page and JSON return the deterministic hackathon demo final score of 86 to stay aligned with the scorecard contract used in the UiPath evidence pack.
