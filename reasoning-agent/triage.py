from __future__ import annotations

from uuid import uuid4

from schemas import EvidenceItem, TriageRequest, TriageResponse


BUSINESS_ACTIONS = {
    "budget_exceeded": "request_purchase_order_approval",
    "vendor_info_missing": "collect_vendor_information",
    "inventory_shortage": "resolve_inventory_shortage",
    "unknown_exception": "manual_investigation",
}

APPROVAL_TYPES = {
    "budget_exceeded": "manager_approval",
    "vendor_info_missing": "data_owner_review",
    "inventory_shortage": "operations_review",
    "unknown_exception": "manual_review",
}


def _correlation_id(payload: TriageRequest) -> str:
    return payload.correlation_id or f"corr_{uuid4().hex}"


def _response_fields(payload: TriageRequest, exception_type: str, next_stage: str) -> dict:
    return {
        "business_action": BUSINESS_ACTIONS[exception_type],
        "required_approval_type": APPROVAL_TYPES[exception_type],
        "recommended_next_stage": next_stage,
        "capability_lookup_required": True,
        "guardrail_status": "passed",
        "memory_references": [],
        "correlation_id": _correlation_id(payload),
        "schema_version": "1.0",
    }


def _vendor_missing(payload: TriageRequest) -> bool:
    return not payload.vendor_info_complete or not (payload.vendor_id or "").strip()


def classify_exception(payload: TriageRequest) -> TriageResponse:
    raw_text = payload.raw_exception_text.lower()

    if payload.amount > payload.budget_limit:
        return TriageResponse(
            case_id=payload.case_id,
            po_id=payload.po_id,
            detected_exception_type="budget_exceeded",
            risk_level="high",
            confidence=0.94,
            recommended_path="manager_approval_required",
            next_action="request_manager_approval",
            requires_human_approval=True,
            next_stage="WAITING_FOR_HUMAN_APPROVAL",
            reasoning_summary=(
                "The purchase order amount exceeds the approved budget limit."
            ),
            evidence=[
                EvidenceItem(field="amount", value=payload.amount),
                EvidenceItem(field="budget_limit", value=payload.budget_limit),
            ],
            **_response_fields(
                payload,
                "budget_exceeded",
                "WAITING_FOR_HUMAN_APPROVAL",
            ),
        )

    if _vendor_missing(payload):
        return TriageResponse(
            case_id=payload.case_id,
            po_id=payload.po_id,
            detected_exception_type="vendor_info_missing",
            risk_level="medium",
            confidence=0.91,
            recommended_path="vendor_information_request",
            next_action="request_vendor_information",
            requires_human_approval=False,
            next_stage="WAITING_VENDOR_INFO",
            reasoning_summary=(
                "Vendor information is missing or incomplete for the purchase order."
            ),
            evidence=[
                EvidenceItem(field="vendor_id", value=payload.vendor_id),
                EvidenceItem(
                    field="vendor_info_complete",
                    value=payload.vendor_info_complete,
                ),
            ],
            **_response_fields(
                payload,
                "vendor_info_missing",
                "WAITING_VENDOR_INFO",
            ),
        )

    if not payload.inventory_available or "inventory shortage" in raw_text:
        return TriageResponse(
            case_id=payload.case_id,
            po_id=payload.po_id,
            detected_exception_type="inventory_shortage",
            risk_level="medium",
            confidence=0.88,
            recommended_path="capability_gap_proposal",
            next_action="create_capability_gap_proposal",
            requires_human_approval=True,
            next_stage="CAPABILITY_GAP_DETECTED",
            reasoning_summary=(
                "Inventory is not available, and the current trusted API capability "
                "does not cover inventory review."
            ),
            evidence=[
                EvidenceItem(
                    field="inventory_available",
                    value=payload.inventory_available,
                ),
                EvidenceItem(
                    field="raw_exception_text",
                    value=payload.raw_exception_text,
                ),
            ],
            **_response_fields(
                payload,
                "inventory_shortage",
                "CAPABILITY_GAP_DETECTED",
            ),
        )

    return TriageResponse(
        case_id=payload.case_id,
        po_id=payload.po_id,
        detected_exception_type="unknown_exception",
        risk_level="unknown",
        confidence=0.4,
        recommended_path="manual_investigation",
        next_action="manual_investigation",
        requires_human_approval=True,
        next_stage="WAITING_MANUAL_INVESTIGATION",
        reasoning_summary=(
            "The exception does not match a supported deterministic Hard MVP route."
        ),
        evidence=[
            EvidenceItem(
                field="raw_exception_text",
                value=payload.raw_exception_text,
            )
        ],
        **_response_fields(
            payload,
            "unknown_exception",
            "WAITING_MANUAL_INVESTIGATION",
        ),
    )
