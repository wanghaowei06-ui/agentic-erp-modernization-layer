# Demo Narration Script

## 0:00-0:30 Problem

"Many ERP modernization projects fail because the starting point is not a clean API. It is a fragile browser workflow with exceptions, approvals, and undocumented business behavior. This demo shows a UiPath-governed path from fragile clicks to a validated API tool for one narrow business action."

## 0:30-1:00 Architecture

"UiPath is the orchestration and governance layer. The Python services in this repo are support assets only: a mock ERP UI, a triage service, a validation suite, and an API facade candidate. UiPath drives the case lifecycle, robot actions, human approvals, validation gate, trusted-tool registration, and API-mode execution."

## 1:00-1:45 Legacy ERP Extraction Through UiPath RPA

"UiPath opens the legacy ERP in Chrome and reads PO-1001 fields from stable HTML IDs. This simulates the realistic starting point: no public business API for the legacy action, so UiPath RPA extracts the evidence safely through the UI."

## 1:45-2:30 Agent Classification And Dynamic Routing

"UiPath sends the extracted fields to the triage support service. PO-1001 returns `budget_exceeded`, high risk, and a human approval stage. We also show PO-1002 as `vendor_info_missing` and PO-1003 as `inventory_shortage` to prove the route is based on detected exception type, not purchase order ID."

## 2:30-3:10 Human Approval And RPA Write-Back

"Because this is a high-risk budget exception, UiPath keeps a human in control. After approval, UiPath uses RPA to write back through the legacy ERP UI by filling the approval reason and manager ID, then clicking the approval request button."

## 3:10-3:50 Validation And Parity Check

"Before switching to API mode, UiPath calls the validation suite. The passed path shows contract, business rule, and RPA/API parity checks. The failed simulation shows what happens when parity fails: UiPath keeps execution mode as RPA and routes the case to IT review."

## 3:50-4:25 Trusted Tool Registration And API Mode Execution

"After validation and trusted-tool approval, UiPath can execute the approved API facade candidate. The response shows the same business result with `execution_mode` set to `API`."

## 4:25-5:00 Dashboard, Timeline, Scorecard, Registry, Roadmap

"The enhanced MVP adds evidence views: a case dashboard, timeline, API readiness scorecard, and tool registry. These are not orchestration engines; they are demo evidence surfaces that make the UiPath-governed modernization lifecycle visible."
