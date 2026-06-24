# Implementation Checklist

Use this checklist inside UiPath Studio while building the demo workflow.

- [ ] Create a UiPath project or process for the modernization case demo.
- [ ] Add required packages and activities, including browser automation, HTTP Request, JSON deserialization, logging, and any approval/task activities used by the tenant.
- [ ] Create all variables from `variables-table.md`.
- [ ] Add browser automation for PO-1001 at `http://localhost:8001/purchase-orders/PO-1001`.
- [ ] Extract fields from stable HTML IDs on the mock ERP detail page.
- [ ] Build the triage JSON body from extracted fields.
- [ ] Call the triage service with `POST http://localhost:8002/triage`.
- [ ] Parse `triage_result_json`.
- [ ] Route by `detected_exception_type`.
- [ ] Implement the `budget_exceeded` route for PO-1001.
- [ ] Show the PO-1002 lightweight route proof using `vendor_info_missing`.
- [ ] Add a business human approval step for routes that require approval.
- [ ] Perform RPA write-back through the legacy ERP UI approval form.
- [ ] Call the validation service with `POST http://localhost:8004/validate/request-purchase-order-approval`.
- [ ] Parse `validation_result_json` and set `validation_status`.
- [ ] Add a trusted tool registration approval placeholder or task.
- [ ] Call the API facade with `POST http://localhost:8003/api/purchase-orders/PO-1001/approval-request`.
- [ ] Parse `api_result_json` and set `execution_mode` to `API`.
- [ ] Build and log `final_case_output_json`.
- [ ] Capture screenshots for the demo: ERP screen, triage output, routing decision, human approval, validation passed, API mode response, final case output.
