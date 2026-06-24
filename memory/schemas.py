from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Capability(BaseModel):
    capability_id: str
    type: str
    business_action: str
    status: str
    validation_status: str
    approved_by: str
    execution_mode: str
    endpoint: str


class CapabilityGap(BaseModel):
    case_id: str
    exception_type: str
    required_business_action: str
    coverage_status: str
    gap_type: str
    manual_resolution_required: bool
    recommended_capability: str
    human_approval_required: bool


JsonDict = dict[str, Any]
