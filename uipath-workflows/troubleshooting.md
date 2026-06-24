# Troubleshooting

## Windows cannot access WSL2 localhost

- Confirm the services are running in WSL2 with `./scripts/start_all.sh`.
- Confirm the services bind to `0.0.0.0` with `ss -ltnp | rg ':800[0-3]'`.
- Try opening `http://localhost:8001/health` in Windows Chrome.
- If localhost forwarding is unavailable, get the WSL2 IP with `hostname -I` and try `http://<wsl-ip>:8000`.

## UiPath cannot scrape fields

- Open `http://localhost:8001/purchase-orders/PO-1001` manually in Chrome.
- Confirm the HTML IDs in `selectors/mock-erp-element-ids.md`.
- Re-pick the element in UiPath Studio and prefer selectors using the `id` attribute.
- Avoid coordinate-based selectors.

## HTTP Request fails

- Check service health endpoints:
  - `http://localhost:8001/health`
  - `http://localhost:8002/health`
  - `http://localhost:8003/health`
- Verify the UiPath HTTP Request method is `POST`.
- Verify the body is valid JSON and the `Content-Type` header is `application/json`.
- Run `./scripts/smoke_test.sh` from WSL2.

## JSON parse fails

- Log the raw HTTP response body before deserialization.
- Confirm UiPath is deserializing object JSON, not array JSON.
- Confirm variable names match the response keys exactly, such as `detected_exception_type` and `requires_human_approval`.

## Chrome extension issue

- Confirm the UiPath browser extension is installed and enabled for Chrome.
- Restart Chrome after installing the extension.
- In UiPath Studio, re-open the browser automation activity and re-indicate the target element.

## Port already in use

- Check listeners with `ss -ltnp | rg ':800[0-3]'`.
- Stop stale processes using the PIDs in `run/*.pid`.
- Restart with `./scripts/start_all.sh`.

## Services not running

- Run `ps -ef | rg 'uvicorn app.main:app'`.
- Inspect logs under `run/`.
- Reinstall dependencies if needed:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## RPA write-back form not found

- Confirm the browser is on `http://localhost:8001/purchase-orders/PO-1001`.
- Confirm the form contains:
  - `approval-reason-input`
  - `manager-id-input`
  - `request-approval-button`
- If the confirmation page is already open, navigate back to the PO detail page.

## API mode returns wrong output

- Confirm the request URL is `http://localhost:8003/api/purchase-orders/PO-1001/approval-request`.
- Confirm the JSON body includes `approval_reason`, `manager_id`, and `source_case_id`.
- Confirm the expected response contains `execution_mode = API`.
- Restart the API facade if demo state needs a clean reset.
