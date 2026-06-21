# Judging Alignment

This project is aligned with UiPath AgentHack Track 1 as a dynamic, exception-heavy workflow where UiPath coordinates agents, robots, and humans.

| Track 1 criterion | Project alignment |
| --- | --- |
| Dynamic exception-heavy workflow | PO-1001, PO-1002, and PO-1003 demonstrate different exception routes. |
| Case-style lifecycle | The implementation pack models intake, extraction, triage, routing, approval, validation, registration, and API-mode execution. |
| Agents, robots, and humans | UiPath robot handles UI automation, triage service supplies structured classification, and humans approve high-risk actions. |
| Human-in-the-loop | Budget and inventory paths require approval or review. Trusted-tool registration is also approval-gated. |
| Stage-based routing | Routing uses `detected_exception_type`, not hardcoded PO IDs. |
| Auditability | Legacy write-back and API-mode responses include status, execution mode, audit evidence, and case timeline views. |
| UiPath as orchestration and governance layer | UiPath remains responsible for case lifecycle, approval, validation governance, and API-mode execution decisions. |
