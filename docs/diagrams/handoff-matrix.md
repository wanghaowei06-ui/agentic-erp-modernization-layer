# Handoff Matrix

| Handoff | Data passed | Owner of next decision |
| --- | --- | --- |
| UiPath -> RPA Robot | Case ID, PO ID, ERP URL | UiPath |
| RPA Robot -> Triage Agent | Scraped ERP fields | UiPath after response |
| Triage Agent -> UiPath | Exception type, risk, confidence, evidence, next stage | UiPath |
| UiPath -> Human | Case context, risk, recommended path | Human approver |
| Human -> UiPath | Approval status and notes | UiPath |
| UiPath -> Validation | Business action and validation request | Validation support service returns evidence |
| Validation -> IT Owner | Pass/fail evidence and recovery recommendation | IT owner / UiPath governance |
| IT Owner -> UiPath | Registration approval or remediation decision | UiPath |
| UiPath -> API Workflow | Approved API-mode request | UiPath |
