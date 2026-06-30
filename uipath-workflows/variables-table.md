# Variables Table

These are the important logical variables in the current ERP Worker flow. The
exact XAML variable names may differ; use `Main.xaml` as the source of truth.

| Variable | Type | Purpose |
| --- | --- | --- |
| `case_id` | String | Business case identifier. |
| `simulation_case_id` | String | Queue item identifier from the demo ERP work queue. |
| `run_id` | String | Run Memory identifier returned by `/memory/runs/start`. |
| `po_id` | String | Purchase order number. |
| `amount` | Number/String | ERP amount extracted from the page. |
| `budget_limit` | Number/String | ERP budget limit extracted from the page. |
| `vendor_id` | String | ERP vendor ID. |
| `vendor_info_complete` | Boolean/String | Vendor data completeness flag. |
| `inventory_available` | Boolean/String | Inventory availability flag. |
| `erp_status` | String | ERP order status. |
| `raw_exception_text` | String | System message / exception reason. |
| `business_remarks` | String | Buyer, manager, or operations remarks. |
| `route_request_json` | String | Body for `/case-intake/route`. |
| `route_response_json` | String | Raw response from `/case-intake/route`. |
| `final_route` | String | Primary branch key returned by the route agent. |
| `policy_decision` | String | Policy gate decision. |
| `agent_context_used` | Boolean | Whether the agent used enterprise context. |
| `company_context_reference` | Object/String | Finance/sales/operations context proof. |
| `agent_reasoning_summary` | String | Human-readable route explanation. |
| `llm_validation_proof` | Object/String | LLM/mock invocation and schema validation proof. |
| `recommended_erp_action` | Object/String | Future-compatible ERP action recommendation. |
| `approval_id` | String | Approval task ID when human approval is required. |
| `selected_erp_action` | String | ERP button/action chosen by UiPath. |
| `memory_commit_response_json` | String | Response from `/memory/runs/{run_id}/commit`. |
