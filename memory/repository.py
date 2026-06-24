from __future__ import annotations

from typing import Any

from memory.schemas import Capability, CapabilityGap
from memory.store import read_json, write_json


CASE_001_TIMELINE = [
    "CASE_CREATED",
    "RPA_EXTRACTED",
    "TRIAGE_COMPLETED",
    "HUMAN_APPROVED",
    "RPA_WRITEBACK_COMPLETED",
    "VALIDATION_PASSED",
    "TRUSTED_TOOL_APPROVED",
    "API_MODE_EXECUTED",
]


def record_case_event(case_id: str, event: str) -> None:
    name = f"case_timeline_{case_id}.json"
    timeline = read_json(name, [])
    if event not in timeline:
        timeline.append(event)
    write_json(name, timeline)
    write_json(
        f"case_state_{case_id}.json",
        {"case_id": case_id, "current_stage": timeline[-1], "timeline": timeline},
    )


def record_agent_decision(case_id: str, decision: dict[str, Any]) -> None:
    write_json(f"agent_decision_{case_id}.json", {"case_id": case_id, **decision})


def record_human_approval(case_id: str, approval: dict[str, Any]) -> None:
    write_json(f"human_approval_{case_id}.json", {"case_id": case_id, **approval})


def record_rpa_trace(case_id: str, trace: dict[str, Any]) -> None:
    write_json(f"rpa_trace_{case_id}.json", {"case_id": case_id, **trace})


def record_validation_result(capability_id: str, result: dict[str, Any]) -> None:
    case_id = str(result.get("case_id", "CASE-001"))
    write_json(
        f"validation_result_{case_id}.json",
        {"capability_id": capability_id, **result},
    )


def register_trusted_capability(capability: dict[str, Any]) -> None:
    validated = Capability(**capability).model_dump()
    write_json("capability_registry.json", validated)


def find_trusted_capability(business_action: str) -> dict[str, Any] | None:
    capability = read_json("capability_registry.json", None)
    if capability and capability.get("business_action") == business_action:
        return capability
    return None


def record_capability_gap(case_id: str, gap: dict[str, Any]) -> None:
    validated = CapabilityGap(case_id=case_id, **gap).model_dump()
    write_json(f"capability_gap_{case_id}.json", validated)


def record_case_001_hard_mvp_artifacts(validation_result: dict[str, Any]) -> None:
    for event in CASE_001_TIMELINE:
        record_case_event("CASE-001", event)
    record_agent_decision(
        "CASE-001",
        {
            "po_id": "PO-1001",
            "detected_exception_type": "budget_exceeded",
            "next_stage": "WAITING_FOR_HUMAN_APPROVAL",
            "next_action": "request_manager_approval",
            "confidence": 0.94,
        },
    )
    record_human_approval(
        "CASE-001",
        {
            "approval_status": "approved",
            "approved_by": "MGR-001",
            "approval_reason": "Amount exceeds budget limit",
        },
    )
    record_rpa_trace(
        "CASE-001",
        {
            "po_id": "PO-1001-RPA",
            "status": "PENDING_MANAGER_APPROVAL",
            "audit_log_created": True,
            "execution_mode": "RPA",
        },
    )
    record_validation_result(
        "request_purchase_order_approval_api",
        {"case_id": "CASE-001", **validation_result},
    )
    register_trusted_capability(
        {
            "capability_id": "request_purchase_order_approval_api",
            "type": "API_TOOL",
            "business_action": "request_purchase_order_approval",
            "status": "trusted",
            "validation_status": "passed",
            "approved_by": "it.owner",
            "execution_mode": "API",
            "endpoint": "POST /api/purchase-orders/{po_id}/approval-request",
        }
    )


def record_inventory_shortage_gap() -> dict[str, Any]:
    gap = {
        "exception_type": "inventory_shortage",
        "required_business_action": "request_inventory_review",
        "coverage_status": "not_covered",
        "gap_type": "missing_workflow",
        "manual_resolution_required": True,
        "recommended_capability": "HandleInventoryShortageReview.xaml",
        "human_approval_required": True,
    }
    record_capability_gap("CASE-003", gap)
    return {"case_id": "CASE-003", **gap}
