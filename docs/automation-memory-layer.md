# Automation Memory Layer

Structured Automation Memory is the Hard MVP audit source. It records case state, timeline events, agent decisions, human approval, RPA trace evidence, validation results, trusted capability registration, and capability gaps.

Local files are written under `memory/data/`:

- `case_state_CASE-001.json`
- `case_timeline_CASE-001.json`
- `agent_decision_CASE-001.json`
- `human_approval_CASE-001.json`
- `rpa_trace_CASE-001.json`
- `validation_result_CASE-001.json`
- `capability_registry.json`
- `capability_gap_CASE-003.json`

The repository API is in `memory/repository.py`:

- `record_case_event(case_id, event)`
- `record_agent_decision(case_id, decision)`
- `record_human_approval(case_id, approval)`
- `record_rpa_trace(case_id, trace)`
- `record_validation_result(capability_id, result)`
- `register_trusted_capability(capability)`
- `find_trusted_capability(business_action)`
- `record_capability_gap(case_id, gap)`

## Boundary

This JSON store is intentionally simple and deterministic for the Hard MVP. It is the source used for demo audit evidence.

LangGraph memory, vector memory, or conversation memory can be added later as enhanced context for agents, but those layers should not replace Structured Automation Memory as the authoritative audit record.
