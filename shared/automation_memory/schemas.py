from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .event_types import MemoryEventType
from .time_utils import utc_now_iso


SCHEMA_VERSION = "1.0"


def new_event_id() -> str:
    return f"evt_{uuid4().hex}"


def new_correlation_id() -> str:
    return f"corr_{uuid4().hex}"


class MemoryEvent(BaseModel):
    event_id: str = Field(default_factory=new_event_id)
    case_id: str
    event_type: MemoryEventType | str
    source_service: str
    schema_version: str = SCHEMA_VERSION
    created_at: str = Field(default_factory=utc_now_iso)
    correlation_id: str = Field(default_factory=new_correlation_id)
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_payload(self) -> "MemoryEvent":
        if not isinstance(self.payload, dict):
            raise TypeError("payload must be a dict")
        return self


class CapabilityRecord(BaseModel):
    capability_id: str
    business_action: str
    capability_type: str
    execution_mode: str
    status: str
    validation_status: str
    schema_version: str = SCHEMA_VERSION
    created_at: str = Field(default_factory=utc_now_iso)
    endpoint: str | None = None
    workflow_name: str | None = None

    @model_validator(mode="after")
    def validate_execution_target(self) -> "CapabilityRecord":
        if not self.endpoint and not self.workflow_name:
            raise ValueError("capability must define endpoint or workflow_name")
        return self
