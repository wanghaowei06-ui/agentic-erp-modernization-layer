# Mock ERP Element IDs

Use these stable HTML IDs in UiPath browser automation. The mock ERP is a legacy UI simulation; the RPA write-back path should click and type through Chrome rather than calling a public business API.

| HTML ID | UiPath usage |
| --- | --- |
| `po-id` | Get Text; assign to `po_id`. |
| `amount` | Get Text; convert to number; assign to `amount`. |
| `budget-limit` | Get Text; convert to number; assign to `budget_limit`. |
| `vendor-id` | Get Text; assign to `vendor_id`. Empty means missing vendor ID. |
| `vendor-info-complete` | Get Text; convert `true` or `false` to Boolean; assign to `vendor_info_complete`. |
| `inventory-available` | Get Text; convert `true` or `false` to Boolean; assign to `inventory_available`. |
| `erp-status` | Get Text; assign to `erp_status`. |
| `raw-exception-text` | Get Text; assign to `raw_exception_text`. |
| `approval-reason-input` | Type Into; enter the business approval reason during RPA write-back. |
| `manager-id-input` | Type Into; enter manager ID such as `MGR-001`. |
| `request-approval-button` | Click; submits the legacy ERP approval request form. |
| `writeback-result` | Get Text; confirms the legacy UI write-back result page loaded. |
| `writeback-status` | Get Text; verify `PENDING_MANAGER_APPROVAL`. |
| `writeback-execution-mode` | Get Text; verify `RPA` for the legacy write-back path. |
| `writeback-audit-created` | Get Text; verify `true`. |

Recommended selector strategy:

- Use UiPath's browser automation recorder to select each element once.
- Prefer selectors that include the HTML `id` attribute.
- Avoid selectors based on row position, text nearby, or visual coordinates.
- Reopen `http://localhost:8000/purchase-orders/PO-1001` before selecting detail page fields.
