from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="ERP Exception Triage Support Service", version="0.1.0")


class TriageRequest(BaseModel):
    case_id: str
    po_id: str
    amount: float
    budget_limit: float
    vendor_id: str | None = None
    vendor_info_complete: bool
    inventory_available: bool
    erp_status: str
    raw_exception_text: str


class TriageResponse(BaseModel):
    case_id: str
    po_id: str
    detected_exception_type: str
    risk_level: str
    confidence: float = Field(ge=0, le=1)
    recommended_path: str
    requires_human_approval: bool
    next_stage: str
    reasoning_summary: str
    evidence: dict[str, Any]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "reasoning-agent"}


@app.post("/triage", response_model=TriageResponse)
def triage(payload: TriageRequest) -> TriageResponse:
    vendor_missing = (
        not payload.vendor_info_complete
        or payload.vendor_id is None
        or payload.vendor_id.strip() == ""
    )

    if payload.amount > payload.budget_limit:
        result = {
            "detected_exception_type": "budget_exceeded",
            "risk_level": "high",
            "confidence": 0.94,
            "recommended_path": "manager_approval_required",
            "requires_human_approval": True,
            "next_stage": "WAITING_FOR_HUMAN_APPROVAL",
            "reasoning_summary": (
                "The purchase order amount exceeds the approved budget limit."
            ),
        }
    elif vendor_missing:
        result = {
            "detected_exception_type": "vendor_info_missing",
            "risk_level": "medium",
            "confidence": 0.91,
            "recommended_path": "vendor_information_request",
            "requires_human_approval": False,
            "next_stage": "WAITING_VENDOR_INFO",
            "reasoning_summary": (
                "Vendor information is incomplete or the vendor ID is missing."
            ),
        }
    elif not payload.inventory_available:
        result = {
            "detected_exception_type": "inventory_shortage",
            "risk_level": "medium",
            "confidence": 0.88,
            "recommended_path": "inventory_review_required",
            "requires_human_approval": True,
            "next_stage": "WAITING_INVENTORY_REVIEW",
            "reasoning_summary": "Inventory is not available for this purchase order.",
        }
    else:
        result = {
            "detected_exception_type": "unknown_exception",
            "risk_level": "unknown",
            "confidence": 0.50,
            "recommended_path": "manual_investigation",
            "requires_human_approval": True,
            "next_stage": "WAITING_MANUAL_INVESTIGATION",
            "reasoning_summary": (
                "No supported deterministic exception rule matched the input fields."
            ),
        }

    evidence = {
        "amount": payload.amount,
        "budget_limit": payload.budget_limit,
        "vendor_id": payload.vendor_id,
        "vendor_info_complete": payload.vendor_info_complete,
        "inventory_available": payload.inventory_available,
        "erp_status": payload.erp_status,
        "raw_exception_text": payload.raw_exception_text,
    }
    return TriageResponse(
        case_id=payload.case_id,
        po_id=payload.po_id,
        evidence=evidence,
        **result,
    )
