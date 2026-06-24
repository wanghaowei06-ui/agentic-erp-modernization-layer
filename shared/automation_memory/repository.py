from __future__ import annotations

from pathlib import Path
from typing import Any

from .event_types import MemoryEventType
from .json_repository import (
    append_event,
    find_capability as json_find_capability,
    query_case_decisions as json_query_case_decisions,
    query_case_timeline as json_query_case_timeline,
    query_gaps as json_query_gaps,
    read_capabilities,
    upsert_capability,
)
from .schemas import CapabilityRecord, MemoryEvent


def _event(
    *,
    case_id: str,
    event_type: MemoryEventType,
    source_service: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> MemoryEvent:
    fields: dict[str, Any] = {
        "case_id": case_id,
        "event_type": event_type,
        "source_service": source_service,
        "payload": payload,
    }
    if correlation_id:
        fields["correlation_id"] = correlation_id
    return MemoryEvent(**fields)


def record_case_event(
    case_id: str,
    event_type: MemoryEventType | str = MemoryEventType.CASE_CREATED,
    payload: dict[str, Any] | None = None,
    *,
    source_service: str = "unknown",
    correlation_id: str | None = None,
    data_dir: str | Path | None = None,
) -> MemoryEvent:
    event = _event(
        case_id=case_id,
        event_type=MemoryEventType(str(event_type)),
        source_service=source_service,
        payload=payload or {},
        correlation_id=correlation_id,
    )
    return append_event(event, data_dir)


def record_evidence(
    case_id: str,
    evidence: dict[str, Any],
    *,
    source_service: str = "mock-legacy-erp",
    correlation_id: str | None = None,
    data_dir: str | Path | None = None,
) -> MemoryEvent:
    return record_case_event(
        case_id,
        MemoryEventType.EVIDENCE_CAPTURED,
        evidence,
        source_service=source_service,
        correlation_id=correlation_id,
        data_dir=data_dir,
    )


def record_agent_decision(
    case_id: str,
    decision: dict[str, Any],
    *,
    event_type: MemoryEventType | str = MemoryEventType.TRIAGE_COMPLETED,
    source_service: str = "reasoning-agent",
    correlation_id: str | None = None,
    data_dir: str | Path | None = None,
) -> MemoryEvent:
    return record_case_event(
        case_id,
        event_type,
        decision,
        source_service=source_service,
        correlation_id=correlation_id,
        data_dir=data_dir,
    )


def record_human_approval(
    case_id: str,
    approval: dict[str, Any],
    *,
    source_service: str = "uipath",
    correlation_id: str | None = None,
    data_dir: str | Path | None = None,
) -> MemoryEvent:
    return record_case_event(
        case_id,
        MemoryEventType.HUMAN_APPROVAL_COMPLETED,
        approval,
        source_service=source_service,
        correlation_id=correlation_id,
        data_dir=data_dir,
    )


def record_execution_trace(
    case_id: str,
    trace: dict[str, Any],
    *,
    execution_mode: str,
    source_service: str,
    correlation_id: str | None = None,
    data_dir: str | Path | None = None,
) -> MemoryEvent:
    event_type = (
        MemoryEventType.API_EXECUTION_COMPLETED
        if execution_mode.upper() == "API"
        else MemoryEventType.RPA_WRITEBACK_COMPLETED
    )
    payload = {"execution_mode": execution_mode, **trace}
    return record_case_event(
        case_id,
        event_type,
        payload,
        source_service=source_service,
        correlation_id=correlation_id,
        data_dir=data_dir,
    )


def record_validation_result(
    case_id: str,
    result: dict[str, Any],
    *,
    source_service: str = "validation-suite",
    correlation_id: str | None = None,
    data_dir: str | Path | None = None,
) -> MemoryEvent:
    return record_case_event(
        case_id,
        MemoryEventType.VALIDATION_COMPLETED,
        result,
        source_service=source_service,
        correlation_id=correlation_id,
        data_dir=data_dir,
    )


def register_capability(
    capability: dict[str, Any] | CapabilityRecord,
    *,
    source_service: str = "validation-suite",
    case_id: str = "CAPABILITY_REGISTRY",
    correlation_id: str | None = None,
    data_dir: str | Path | None = None,
) -> CapabilityRecord:
    record = (
        capability
        if isinstance(capability, CapabilityRecord)
        else CapabilityRecord(**capability)
    )
    upsert_capability(record, data_dir)
    record_case_event(
        case_id,
        MemoryEventType.CAPABILITY_REGISTERED,
        record.model_dump(),
        source_service=source_service,
        correlation_id=correlation_id,
        data_dir=data_dir,
    )
    return record


def find_capability(
    business_action: str,
    *,
    data_dir: str | Path | None = None,
) -> CapabilityRecord | None:
    return json_find_capability(business_action, data_dir)


def record_capability_gap(
    case_id: str,
    gap: dict[str, Any],
    *,
    source_service: str = "validation-suite",
    correlation_id: str | None = None,
    data_dir: str | Path | None = None,
) -> MemoryEvent:
    return record_case_event(
        case_id,
        MemoryEventType.CAPABILITY_GAP_RECORDED,
        gap,
        source_service=source_service,
        correlation_id=correlation_id,
        data_dir=data_dir,
    )


def query_case_timeline(
    case_id: str,
    *,
    data_dir: str | Path | None = None,
) -> list[MemoryEvent]:
    return json_query_case_timeline(case_id, data_dir)


def query_case_decisions(
    case_id: str,
    *,
    data_dir: str | Path | None = None,
) -> list[MemoryEvent]:
    return json_query_case_decisions(case_id, data_dir)


def query_capabilities(
    *,
    data_dir: str | Path | None = None,
) -> list[CapabilityRecord]:
    return read_capabilities(data_dir)


def query_gaps(
    *,
    data_dir: str | Path | None = None,
) -> list[MemoryEvent]:
    return json_query_gaps(data_dir)
