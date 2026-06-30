# HTTP Request Bodies

Use these files as UiPath HTTP Request body templates.

## Current Route Agent Endpoint

Set `Content-Type` to `application/json` and call:

```text
POST http://localhost:8002/case-intake/route
```

| File | Scenario |
| --- | --- |
| `case-intake-route-po-1000.json` | Normal deterministic precheck, no agent required. |
| `case-intake-route-po-1001.json` | Budget exceeded, enterprise context agent path, human approval. |
| `case-intake-route-po-1002.json` | Vendor information missing, wait for vendor data. |
| `case-intake-route-po-1003.json` | Inventory shortage, capability gap / XAML workflow proposal evidence. |
| `case-intake-route-po-1004.json` | Ambiguous case, manual investigation. |

## Other Support Calls

| File | Method and endpoint |
| --- | --- |
| `validation-request.json` | `POST http://localhost:8004/validate/request-purchase-order-approval` |
| `validation-failed-request.json` | `POST http://localhost:8004/validate/request-purchase-order-approval` |
| `api-mode-request.json` | `POST http://localhost:8003/api/purchase-orders/PO-1001-API/approval-request` |

The legacy `/triage` endpoint is still available for compatibility tests, but
the current UiPath worker should use `/case-intake/route`.
