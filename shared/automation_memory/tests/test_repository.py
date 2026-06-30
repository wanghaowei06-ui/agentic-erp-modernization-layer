from __future__ import annotations

import json

import pytest

from shared.automation_memory.event_types import MemoryEventType
from shared.automation_memory.json_repository import AutomationMemoryJsonError
from shared.automation_memory.repository import (
    find_capability,
    query_capabilities,
    query_case_decisions,
    query_case_timeline,
    query_gaps,
    record_agent_decision,
    record_capability_gap,
    record_case_event,
    register_capability,
)


def test_record_case_event_writes_memory_event(tmp_path):
    event = record_case_event(
        "CASE-001",
        MemoryEventType.CASE_CREATED,
        {"po_id": "PO-1001"},
        source_service="pytest",
        data_dir=tmp_path,
    )

    assert event.event_id.startswith("evt_")
    assert event.case_id == "CASE-001"
    assert event.event_type == MemoryEventType.CASE_CREATED
    assert event.source_service == "pytest"
    assert event.schema_version == "1.0"
    assert event.created_at.endswith("Z")
    assert event.correlation_id.startswith("corr_")
    assert event.payload == {"po_id": "PO-1001"}
    assert (tmp_path / "events.db").exists()


def test_record_agent_decision_writes_triage_completed(tmp_path):
    event = record_agent_decision(
        "CASE-001",
        {
            "detected_exception_type": "budget_exceeded",
            "confidence": 0.94,
        },
        data_dir=tmp_path,
    )

    assert event.event_type == MemoryEventType.TRIAGE_COMPLETED
    assert event.source_service == "reasoning-agent"
    assert event.payload["detected_exception_type"] == "budget_exceeded"


def test_query_case_timeline_returns_events_in_created_order(tmp_path):
    record_case_event(
        "CASE-001",
        MemoryEventType.VALIDATION_COMPLETED,
        {"status": "passed"},
        source_service="pytest",
        data_dir=tmp_path,
    )
    record_case_event(
        "CASE-002",
        MemoryEventType.CASE_CREATED,
        {},
        source_service="pytest",
        data_dir=tmp_path,
    )
    record_case_event(
        "CASE-001",
        MemoryEventType.CASE_CREATED,
        {},
        source_service="pytest",
        data_dir=tmp_path,
    )

    timeline = query_case_timeline("CASE-001", data_dir=tmp_path)

    assert [event.case_id for event in timeline] == ["CASE-001", "CASE-001"]
    assert timeline == sorted(timeline, key=lambda event: event.created_at)


def test_query_case_decisions_returns_agent_decision_events(tmp_path):
    record_case_event(
        "CASE-001",
        MemoryEventType.CASE_CREATED,
        {},
        source_service="pytest",
        data_dir=tmp_path,
    )
    record_agent_decision(
        "CASE-001",
        {"detected_exception_type": "budget_exceeded"},
        data_dir=tmp_path,
    )
    record_agent_decision(
        "CASE-001",
        {"readiness_score": 0.91},
        event_type=MemoryEventType.READINESS_ASSESSED,
        source_service="reasoning-agent",
        data_dir=tmp_path,
    )

    decisions = query_case_decisions("CASE-001", data_dir=tmp_path)

    assert [event.event_type for event in decisions] == [
        MemoryEventType.TRIAGE_COMPLETED,
        MemoryEventType.READINESS_ASSESSED,
    ]


def test_register_capability_stores_multiple_capabilities_as_list(tmp_path):
    register_capability(
        {
            "capability_id": "cap_api_request_po_approval_v1",
            "business_action": "request_purchase_order_approval",
            "capability_type": "api",
            "execution_mode": "API",
            "endpoint": (
                "http://localhost:8003/api/purchase-orders/{po_id}/approval-request"
            ),
            "status": "trusted",
            "validation_status": "passed",
        },
        data_dir=tmp_path,
    )
    register_capability(
        {
            "capability_id": "cap_workflow_vendor_info_v1",
            "business_action": "request_vendor_information",
            "capability_type": "workflow",
            "execution_mode": "RPA",
            "workflow_name": "HandleVendorInfoMissing.xaml",
            "status": "draft",
            "validation_status": "pending",
        },
        data_dir=tmp_path,
    )

    capabilities = query_capabilities(data_dir=tmp_path)
    raw = json.loads((tmp_path / "capabilities.json").read_text())

    assert len(capabilities) == 2
    assert isinstance(raw, list)
    assert raw[0]["capability_id"] == "cap_api_request_po_approval_v1"


def test_find_capability_returns_only_trusted_and_passed(tmp_path):
    register_capability(
        {
            "capability_id": "cap_failed",
            "business_action": "request_purchase_order_approval",
            "capability_type": "api",
            "execution_mode": "API",
            "endpoint": "http://localhost:8003/fail",
            "status": "trusted",
            "validation_status": "failed",
        },
        data_dir=tmp_path,
    )
    register_capability(
        {
            "capability_id": "cap_trusted",
            "business_action": "request_purchase_order_approval",
            "capability_type": "api",
            "execution_mode": "API",
            "endpoint": "http://localhost:8003/pass",
            "status": "trusted",
            "validation_status": "passed",
        },
        data_dir=tmp_path,
    )

    match = find_capability(
        "request_purchase_order_approval",
        data_dir=tmp_path,
    )

    assert match is not None
    assert match.capability_id == "cap_trusted"
    assert find_capability("unknown_action", data_dir=tmp_path) is None


def test_record_capability_gap_and_query_gaps(tmp_path):
    event = record_capability_gap(
        "CASE-003",
        {
            "exception_type": "inventory_shortage",
            "required_business_action": "request_inventory_review",
            "coverage_status": "not_covered",
        },
        data_dir=tmp_path,
    )

    gaps = query_gaps(data_dir=tmp_path)

    assert event.event_type == MemoryEventType.CAPABILITY_GAP_RECORDED
    assert len(gaps) == 1
    assert gaps[0].case_id == "CASE-003"
    assert gaps[0].payload["coverage_status"] == "not_covered"


def test_memory_data_dir_is_created_automatically(tmp_path):
    nested_dir = tmp_path / "missing" / "memory"

    record_case_event(
        "CASE-001",
        MemoryEventType.CASE_CREATED,
        {},
        source_service="pytest",
        data_dir=nested_dir,
    )

    assert nested_dir.exists()
    assert (nested_dir / "case_snapshots").exists()
    assert (nested_dir / "events.db").exists()


def test_damaged_capability_json_raises_clear_error(tmp_path):
    (tmp_path / "capabilities.json").write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(AutomationMemoryJsonError, match="Invalid JSON"):
        query_capabilities(data_dir=tmp_path)


def test_sqlite_backend_indexes_events_by_case_without_full_scan(tmp_path):
    record_case_event(
        "CASE-001",
        MemoryEventType.CASE_CREATED,
        {},
        source_service="pytest",
        data_dir=tmp_path,
    )
    record_case_event(
        "CASE-002",
        MemoryEventType.CASE_CREATED,
        {},
        source_service="pytest",
        data_dir=tmp_path,
    )
    record_case_event(
        "CASE-001",
        MemoryEventType.VALIDATION_COMPLETED,
        {},
        source_service="pytest",
        data_dir=tmp_path,
    )

    timeline = query_case_timeline("CASE-001", data_dir=tmp_path)
    assert [event.case_id for event in timeline] == ["CASE-001", "CASE-001"]


def test_export_events_jsonl_round_trips_sqlite_events(tmp_path):
    from shared.automation_memory.json_repository import (
        read_events_jsonl,
    )
    from shared.automation_memory.sqlite_store import export_events_jsonl

    record_case_event(
        "CASE-001",
        MemoryEventType.CASE_CREATED,
        {"po_id": "PO-1001"},
        source_service="pytest",
        data_dir=tmp_path,
    )

    exported = export_events_jsonl(data_dir=tmp_path)
    assert exported.exists()
    legacy = read_events_jsonl(data_dir=tmp_path)
    assert len(legacy) == 1
    assert legacy[0].case_id == "CASE-001"
    assert legacy[0].payload == {"po_id": "PO-1001"}


def test_migrate_from_jsonl_is_idempotent(tmp_path):
    from shared.automation_memory.json_repository import read_events_jsonl
    from shared.automation_memory.sqlite_store import (
        migrate_from_jsonl,
        select_all_events,
    )

    source = tmp_path / "events.jsonl"
    source.write_text(
        '{"event_id":"evt_a","case_id":"CASE-001","event_type":"CASE_CREATED",'
        '"source_service":"legacy","schema_version":"1.0","created_at":"2026-01-01T00:00:00Z",'
        '"correlation_id":"corr_a","payload":{"po_id":"PO-1001"}}\n',
        encoding="utf-8",
    )

    migrated_first, _ = migrate_from_jsonl(data_dir=tmp_path)
    assert migrated_first == 1
    assert len(select_all_events(data_dir=tmp_path)) == 1

    # Running again must not duplicate events.
    migrated_second, _ = migrate_from_jsonl(data_dir=tmp_path)
    assert migrated_second == 0
    assert len(select_all_events(data_dir=tmp_path)) == 1
    # Source JSONL is untouched by the library function (script renames it).
    assert read_events_jsonl(data_dir=tmp_path)[0].event_id == "evt_a"


def test_find_similar_cases_matches_exception_type_and_business_action(tmp_path):
    from shared.automation_memory.repository import find_similar_cases

    # Two inventory_shortage gaps for the same business action.
    record_capability_gap(
        "CASE-003",
        {
            "exception_type": "inventory_shortage",
            "required_business_action": "request_inventory_review",
            "coverage_status": "not_covered",
        },
        data_dir=tmp_path,
    )
    record_capability_gap(
        "CASE-007",
        {
            "exception_type": "inventory_shortage",
            "required_business_action": "request_inventory_review",
            "coverage_status": "not_covered",
        },
        data_dir=tmp_path,
    )
    # A different exception type for the same business action.
    record_capability_gap(
        "CASE-008",
        {
            "exception_type": "vendor_info_missing",
            "required_business_action": "request_inventory_review",
            "coverage_status": "not_covered",
        },
        data_dir=tmp_path,
    )
    # A different business action entirely.
    record_capability_gap(
        "CASE-009",
        {
            "exception_type": "inventory_shortage",
            "required_business_action": "request_purchase_order_approval",
            "coverage_status": "not_covered",
        },
        data_dir=tmp_path,
    )

    similar = find_similar_cases(
        "inventory_shortage",
        "request_inventory_review",
        data_dir=tmp_path,
    )

    assert len(similar) == 2
    assert {event.case_id for event in similar} == {"CASE-003", "CASE-007"}


def test_find_similar_cases_matches_legacy_nested_payload(tmp_path):
    """Gap payloads written by validation-suite nest the original gap payload."""
    from shared.automation_memory.repository import find_similar_cases

    record_capability_gap(
        "CASE-003",
        {
            "business_action": "resolve_inventory_shortage",
            "gap_type": "missing_trusted_capability",
            "legacy_gap_payload": {
                "exception_type": "inventory_shortage",
                "required_business_action": "request_inventory_review",
            },
        },
        data_dir=tmp_path,
    )

    similar = find_similar_cases(
        "inventory_shortage",
        "request_inventory_review",
        data_dir=tmp_path,
    )
    assert len(similar) == 1
    assert similar[0].case_id == "CASE-003"


def test_count_repeated_gaps_filters_by_exception_type(tmp_path):
    from shared.automation_memory.repository import count_repeated_gaps

    for case_id in ("CASE-003", "CASE-007"):
        record_capability_gap(
            case_id,
            {
                "exception_type": "inventory_shortage",
                "required_business_action": "request_inventory_review",
            },
            data_dir=tmp_path,
        )
    record_capability_gap(
        "CASE-008",
        {
            "exception_type": "vendor_info_missing",
            "required_business_action": "request_inventory_review",
        },
        data_dir=tmp_path,
    )

    # Without gap_type filter -> all three gaps for the business action.
    assert count_repeated_gaps("request_inventory_review", data_dir=tmp_path) == 3
    # With gap_type filter -> only the two inventory_shortage gaps.
    assert (
        count_repeated_gaps(
            "request_inventory_review",
            gap_type="inventory_shortage",
            data_dir=tmp_path,
        )
        == 2
    )
