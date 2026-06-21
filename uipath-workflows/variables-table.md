# Variables Table

| Variable name | Type | Default value | Purpose |
| --- | --- | --- | --- |
| `case_id` | String | `CASE-001` | UiPath-governed case identifier. |
| `po_id` | String | `PO-1001` | Purchase order selected for the main demo path. |
| `current_stage` | String | `CASE_INTAKE` | Current stage in the UiPath case flow. |
| `amount` | Double | `0` | Amount scraped from the ERP UI. |
| `budget_limit` | Double | `0` | Budget limit scraped from the ERP UI. |
| `vendor_id` | String | `""` | Vendor ID scraped from the ERP UI. |
| `vendor_info_complete` | Boolean | `False` | Vendor completeness flag scraped from the ERP UI. |
| `inventory_available` | Boolean | `False` | Inventory availability flag scraped from the ERP UI. |
| `erp_status` | String | `""` | ERP status scraped from the ERP UI. |
| `raw_exception_text` | String | `""` | Raw exception text scraped from the ERP UI. |
| `triage_result_json` | String | `""` | Raw JSON response from the triage service. |
| `detected_exception_type` | String | `""` | Parsed routing key from triage output. |
| `risk_level` | String | `""` | Parsed risk level from triage output. |
| `requires_human_approval` | Boolean | `False` | Parsed approval requirement from triage output. |
| `next_stage` | String | `""` | Parsed recommended next stage from triage output. |
| `human_approval_status` | String | `pending` | Business approval result controlled by UiPath. |
| `execution_mode` | String | `RPA` | Current execution mode selected by UiPath. |
| `validation_status` | String | `not_started` | Validation gate status controlled by UiPath. |
| `trusted_tool_status` | String | `not_registered` | Trusted-tool registration status controlled by UiPath. |
| `validation_result_json` | String | `""` | Raw JSON response from the validation support service. |
| `api_result_json` | String | `""` | Raw JSON response from the API facade candidate. |
| `final_case_output_json` | String | `""` | Final case summary logged by UiPath. |
