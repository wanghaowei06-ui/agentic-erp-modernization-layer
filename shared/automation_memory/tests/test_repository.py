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
    assert (tmp_path / "events.jsonl").exists()


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
    assert (nested_dir / "events.jsonl").exists()


def test_damaged_json_raises_clear_error(tmp_path):
    (tmp_path / "events.jsonl").write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(AutomationMemoryJsonError, match="Invalid JSONL event"):
        query_case_timeline("CASE-001", data_dir=tmp_path)
