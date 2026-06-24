# HTTP Request Bodies

Use these files as UiPath HTTP Request body templates.

| File | Method and endpoint |
| --- | --- |
| `triage-po-1001.json` | `POST http://localhost:8002/triage` |
| `triage-po-1002.json` | `POST http://localhost:8002/triage` |
| `triage-po-1003.json` | `POST http://localhost:8002/triage` |
| `validation-request.json` | `POST http://localhost:8004/validate/request-purchase-order-approval` |
| `validation-failed-request.json` | `POST http://localhost:8004/validate/request-purchase-order-approval` |
| `api-mode-request.json` | `POST http://localhost:8003/api/purchase-orders/PO-1001/approval-request` |

Set `Content-Type` to `application/json` for the triage and API facade calls. The validation request can use an empty JSON object.
