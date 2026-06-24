from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def new_correlation_id() -> str:
    return f"corr_{uuid4().hex}"


class EvidenceItem(BaseModel):
    field: str
    value: Any
    source: str = "legacy_erp_screen"


class TriageRequest(BaseModel):
    case_id: str
    po_id: str
    correlation_id: str | None = None
    amount: int | float
    budget_limit: int | float
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
    next_action: str
    requires_human_approval: bool
    next_stage: str
    reasoning_summary: str
    evidence: list[EvidenceItem]
    fallback: str = "manual_investigation"
    decision_source: str = "deterministic_rule"
    business_action: str = "manual_investigation"
    required_approval_type: str = "manual_review"
    recommended_next_stage: str = "WAITING_MANUAL_INVESTIGATION"
    capability_lookup_required: bool = True
    guardrail_status: str = "passed"
    memory_references: list[dict[str, Any]] = []
    correlation_id: str = Field(default_factory=new_correlation_id)
    schema_version: str = "1.0"
