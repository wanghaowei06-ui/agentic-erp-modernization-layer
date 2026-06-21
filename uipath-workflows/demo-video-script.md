# Demo Video Script

## 0:00 - 0:30 Problem

UiPath starts with a common enterprise problem: a critical ERP approval process is trapped behind fragile browser clicks. The goal is not to replace the ERP in one step. The goal is to let UiPath govern a modernization case that safely moves one validated business action from RPA execution toward API-mode execution.

## 0:30 - 1:20 Dynamic Case Intake

UiPath creates the modernization case for `CASE-001` and opens the mock legacy ERP in Chrome. UiPath RPA extracts PO-1001 fields from stable UI element IDs: amount, budget limit, vendor information, inventory availability, ERP status, and raw exception text.

The Python services are support assets only. UiPath remains the case owner and execution controller.

## 1:20 - 2:00 Agent Classification and Routing

UiPath calls the exception triage support service over HTTP. For PO-1001, the service returns `budget_exceeded`, `high` risk, and `WAITING_FOR_HUMAN_APPROVAL`.

UiPath routes the case using `detected_exception_type`, not the purchase order ID. To prove dynamic routing, UiPath also shows the PO-1002 route where the triage result is `vendor_info_missing` and human approval is not required.

## 2:00 - 2:40 Human Approval and RPA Write-back

UiPath handles the business approval step. After approval, UiPath returns to the legacy ERP UI and performs the write-back through browser automation: it fills the approval reason, enters the manager ID, and clicks the approval request button.

The confirmation page shows `PENDING_MANAGER_APPROVAL`, `RPA`, and audit creation.

## 2:40 - 3:30 API Candidate and Validation

UiPath calls the validation support service before trusting the API facade. The validation gate shows contract test passed, business rule test passed, and RPA/API parity passed.

The parity check uses cloned reset test cases: `PO-1001-RPA` and `PO-1001-API`. UiPath does not compare the API result against a record already mutated by the RPA path.

## 3:30 - 4:20 API Mode Execution

After validation and trusted-tool registration approval, UiPath switches the approved case to API mode. UiPath calls the generated API facade candidate for `request_purchase_order_approval`.

The response shows `PENDING_MANAGER_APPROVAL`, `audit_log_created = true`, and `execution_mode = API`.

## 4:20 - 5:00 Impact and Roadmap

UiPath has governed the full modernization journey: intake, extraction, triage, routing, human approval, RPA write-back, validation, trusted-tool approval, and API-mode execution.

The impact is a practical path from fragile ERP clicks to validated, human-approved API tools. The roadmap is to repeat this pattern for more high-frequency actions, raise validation coverage, and register trusted tools only when UiPath governance approves them.
