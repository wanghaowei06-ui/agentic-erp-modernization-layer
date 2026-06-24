# Mock ERP Element IDs

Use UiPath browser automation against the legacy-style ERP page. The page intentionally avoids obvious automation-only attributes such as `data-uipath`, `data-testid`, or `automation-id`.

Prefer the legacy WebForms-style IDs below when reselecting elements in UiPath Studio.

| Recommended legacy ID | UiPath usage |
| --- | --- |
| `ctl00_MainContent_lblPoNumber` | Get Text; assign to `po_id`. |
| `ctl00_MainContent_lblAmount` | Get Text; convert to number; assign to `amount`. |
| `ctl00_MainContent_lblBudgetLimit` | Get Text; convert to number; assign to `budget_limit`. |
| `ctl00_MainContent_lblVendorId` | Get Text; assign to `vendor_id`. Empty means missing vendor ID. |
| `ctl00_MainContent_lblVendorInfoComplete` | Get Text; convert `true` or `false` to Boolean; assign to `vendor_info_complete`. |
| `ctl00_MainContent_lblInventoryAvailable` | Get Text; convert `true` or `false` to Boolean; assign to `inventory_available`. |
| `ctl00_MainContent_lblStatus` | Get Text; assign to `erp_status`. |
| `ctl00_MainContent_lblExceptionReason` | Get Text; assign to `raw_exception_text`. |
| `ctl00_MainContent_txtApprovalReason` | Type Into; enter the business approval reason during RPA write-back. |
| `ctl00_MainContent_txtManagerId` | Type Into; enter manager ID such as `MGR-001`. |
| `ctl00_MainContent_btnRequestApproval` | Click; submits the legacy ERP approval request form. |
| `ctl00_MainContent_lblWritebackStatus` | Get Text; verify `PENDING_MANAGER_APPROVAL` after write-back. |
| `ctl00_MainContent_lblExecutionMode` | Get Text; verify `RPA` on the write-back result page. |
| `ctl00_MainContent_lblAuditCreated` | Get Text; verify `true` on the write-back result page. |

Compatibility IDs retained for older workflow templates:

| Compatibility ID | UiPath usage |
| --- | --- |
| `po-id` | Get Text; assign to `po_id`. |
| `amount` | Get Text; convert to number; assign to `amount`. |
| `budget-limit` | Get Text; convert to number; assign to `budget_limit`. |
| `vendor-id` | Get Text; assign to `vendor_id`. |
| `vendor-info-complete` | Get Text; convert to Boolean. |
| `inventory-available` | Get Text; convert to Boolean. |
| `erp-status` | Get Text; assign to `erp_status`. |
| `raw-exception-text` | Get Text; assign to `raw_exception_text`. |
| `approval-reason-input` | Compatibility marker for the approval reason field. |
| `manager-id-input` | Compatibility marker for the manager ID field. |
| `request-approval-button` | Compatibility marker for the submit action. |
| `writeback-result` | Get Text; confirms the write-back result page loaded. |
| `writeback-status` | Get Text; verify `PENDING_MANAGER_APPROVAL`. |
| `writeback-execution-mode` | Get Text; verify `RPA`. |
| `writeback-audit-created` | Get Text; verify `true`. |

Recommended selector strategy:

- Use UiPath's browser automation recorder to select each element once.
- Prefer selectors that include the legacy-style HTML `id` attribute.
- Avoid selectors based on row position, nearby text, or visual coordinates.
- Reopen `http://localhost:8001/purchase-orders/PO-1001` before selecting detail page fields.
