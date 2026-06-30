# Capability Evolution Loop

## Function

The Capability Evolution Loop (PRD 18.2) is the mechanism by which the ERP
modernization layer accumulates new automation capabilities from repeated case
patterns. When the same uncovered business action is observed enough times, the
system proposes a new capability (workflow / API / skill) for governed review,
instead of leaving the work permanently on the manual backlog.

The loop is **propose-only**. It never auto-generates, approves, publishes, or
executes new code. Every proposal still flows through the standard human review,
validation, and registration gates (PRD 18.5).

## Implementation Principle

The trigger lives in the `reasoning-agent` service and is driven by the
Automation Memory event store.

```text
                     case runs without a trusted capability
                                   |
                   record_capability_gap(business_action)
                                   |
                                   v
               count_repeated_gaps(business_action)
                                   |
                count >= CAPABILITY_EVOLUTION_THRESHOLD ?
                      /                          \
                   no                            yes
                   |                              |
        return triggered=false       run_plan_agent(business_action)
                                   |
                                   v
                      modernization plan (recommendation)
                                   |
                      human review -> validation -> register
```

Key components:

- **Gap recording.** When a case cannot be matched to a trusted capability, a
  `CAPABILITY_GAP_RECORDED` event is appended to the memory store. The payload
  includes `required_business_action` so gaps can be grouped by business action.
- **Repeated-gap counter.** `shared.automation_memory.repository.count_repeated_gaps`
  counts gap events for a given business action. The SQLite backend indexes on
  `event_type` and uses `json_extract(payload, '$.required_business_action')`,
  so the count is an indexed lookup rather than a full-table scan.
- **Threshold gate.** The reasoning-agent reads the threshold from the
  `CAPABILITY_EVOLUTION_THRESHOLD` environment variable (default `3`). A value
  below `1` is clamped to `1`. Invalid values fall back to the default with a
  warning log entry.
- **Plan generation.** When the count reaches the threshold, the agent
  constructs a `ModernizationPlanRequest` for the uncovered business action and
  calls `run_plan_agent`, the same function exposed by
  `POST /modernization/plan`.
- **Logging.** Every evaluation logs the business action, current gap count and
  threshold. Trigger fires and plan outcomes (plan id, target tool, recommended
  next stage) are logged separately so operators can audit the loop.
- **Containment.** Plan generation failures are caught, logged, and returned in
  the response payload; they never propagate to block the calling flow.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `CAPABILITY_EVOLUTION_THRESHOLD` | `3` | Minimum number of repeated gaps for a business action before `run_plan_agent` is triggered. |
| `AUTOMATION_MEMORY_DIR` | `memory-data` | Directory containing `events.db`. |
| `MEMORY_WRITE_API_KEY` | unset | When set, gap-recording endpoints require this token (PRD 17.6). |

## Interface Definition

### HTTP

`POST /capability-evolution/evaluate` (reasoning-agent)

Request body:

```json
{
  "business_action": "request_inventory_review",
  "case_id": "CASE-003"
}
```

- `business_action` (string, required) — the uncovered business action to
  evaluate.
- `case_id` (string, optional) — the originating case. When omitted a synthetic
  id `CAPABILITY_EVOLUTION_<business_action>` is used for the generated plan.

Response body (`200 OK`):

```json
{
  "triggered": true,
  "business_action": "request_inventory_review",
  "repeated_gaps": 3,
  "threshold": 3,
  "plan": {
    "plan_id": "MOD-PLAN-001",
    "case_id": "CASE-003",
    "target_tool_name": "request_inventory_review",
    "recommended_next_stage": "AUTOMATION_OWNER_PLAN_REVIEW"
  }
}
```

When the threshold is not reached, `triggered` is `false` and `plan` is omitted
(the response uses `response_model_exclude_none`).

When the threshold is reached but plan generation fails, `triggered` is `true`,
`plan` is omitted, and `error` contains the failure message.

### Python

```python
from shared.automation_memory.repository import count_repeated_gaps

count_repeated_gaps("request_inventory_review")  # -> int
```

```python
from app.main import maybe_trigger_capability_evolution

result = maybe_trigger_capability_evolution(
    "request_inventory_review",
    case_id="CASE-003",
)
# result = {"triggered": bool, "business_action": str, "repeated_gaps": int,
#           "threshold": int, "plan": ModernizationPlanResponse | None}
```

## Usage Example

Record three capability gaps for the same uncovered business action, then
evaluate the trigger:

```python
from shared.automation_memory.repository import record_capability_gap

for case_id in ("CASE-003", "CASE-004", "CASE-005"):
    record_capability_gap(
        case_id,
        {
            "exception_type": "inventory_shortage",
            "required_business_action": "request_inventory_review",
            "coverage_status": "not_covered",
        },
        source_service="validation-suite",
    )
```

```bash
curl -s -X POST http://localhost:8002/capability-evolution/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"business_action":"request_inventory_review","case_id":"CASE-003"}'
```

Response:

```json
{
  "triggered": true,
  "business_action": "request_inventory_review",
  "repeated_gaps": 3,
  "threshold": 3,
  "plan": {
    "plan_id": "MOD-PLAN-001",
    "case_id": "CASE-003",
    "target_tool_name": "request_inventory_review",
    "target_service": "generated-api-facade",
    "proposed_endpoint": "POST /api/inventory/review",
    "source_rpa_trace": "...",
    "contract_requirements": ["must return sku and available_qty"],
    "tests_required": ["contract_test", "business_rule_test", "rpa_api_parity_check"],
    "risk_level": "medium",
    "requires_engineer_approval": true,
    "recommended_next_stage": "AUTOMATION_OWNER_PLAN_REVIEW"
  }
}
```

## Safety Boundary (PRD 18.5)

The Capability Evolution Loop only **proposes** capabilities. A proposal becomes
a trusted, reusable capability only after:

1. **Human review** of the generated plan by the automation owner / engineer.
2. **Validation** through the validation-suite (contract, business-rule, and
   RPA/API parity checks).
3. **Registration** via `register_trusted_capability()`, which is gated by the
   API-key dependency (PRD 17.6).

Until those steps complete, the business action remains uncovered and continues
to be flagged as a capability gap on subsequent cases.
