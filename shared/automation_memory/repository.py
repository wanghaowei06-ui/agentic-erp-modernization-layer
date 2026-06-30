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


def count_repeated_gaps(
    business_action: str,
    gap_type: str | None = None,
    *,
    data_dir: str | Path | None = None,
) -> int:
    """Count capability-gap events for a business action (PRD 17.6 / 18.2).

    Signature follows PRD 17.6 ``count_repeated_gaps(gap_type, business_action)``.
    ``business_action`` is the primary key (kept first for backward
    compatibility with the Capability Evolution Loop trigger). ``gap_type`` is
    the optional ``exception_type`` filter (e.g. ``inventory_shortage``); when
    omitted, all gap events for the business action are counted.

    Used by the Capability Evolution Loop trigger: when the number of repeated
    gaps for an uncovered ``business_action`` reaches the configured threshold,
    the system proposes a new capability via ``run_plan_agent``.
    """
    from .sqlite_store import count_gaps_by_business_action

    return count_gaps_by_business_action(
        business_action,
        data_dir,
        exception_type=gap_type,
    )


def find_similar_cases(
    exception_type: str,
    business_action: str,
    *,
    data_dir: str | Path | None = None,
) -> list[MemoryEvent]:
    """Return past capability-gap cases matching the same exception + action.

    Implements PRD 17.6 ``find_similar_cases(exception_type, business_action)``.
    Matching is delegated to the SQLite store and uses ``json_extract`` on the
    ``CAPABILITY_GAP_RECORDED`` payload so it is index-backed rather than a
    Python full-table scan.
    """
    from .sqlite_store import find_similar_cases as _find

    return _find(exception_type, business_action, data_dir)
