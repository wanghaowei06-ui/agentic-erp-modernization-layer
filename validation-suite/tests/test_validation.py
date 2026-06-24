import sys
from pathlib import Path

from fastapi.testclient import TestClient


def load_app(monkeypatch=None, memory_dir=None):
    service_root = Path(__file__).resolve().parents[1]
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    if monkeypatch is not None and memory_dir is not None:
        monkeypatch.setenv("AUTOMATION_MEMORY_DIR", str(memory_dir))
    elif monkeypatch is not None:
        monkeypatch.delenv("AUTOMATION_MEMORY_DIR", raising=False)
    sys.path.insert(0, str(service_root))
    from app.main import app

    return app


def test_validation_gate_returns_passed_result(monkeypatch, tmp_path):
    response = TestClient(load_app(monkeypatch, tmp_path)).post(
        "/validate/request-purchase-order-approval"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "CASE-001"
    assert body["data_isolation"] == "cloned_test_cases"
    assert body["rpa_test_case_id"] == "PO-1001-RPA"
    assert body["api_test_case_id"] == "PO-1001-API"
    assert body["contract_test"] == "passed"
    assert body["business_rule_test"] == "passed"
    assert body["rpa_api_parity_check"] == "passed"
    assert body["same_initial_state"] is True
    assert body["rpa_result"] == {
        "status": "PENDING_MANAGER_APPROVAL",
        "audit_log_created": True,
    }
    assert body["api_result"] == {
        "status": "PENDING_MANAGER_APPROVAL",
        "audit_log_created": True,
    }
    assert body["matched_side_effects"] == [
        "PO_STATUS_UPDATED",
        "APPROVAL_TASK_CREATED",
        "AUDIT_LOG_CREATED",
        "MANAGER_NOTIFICATION_QUEUED",
        "BUDGET_REVIEW_FLAGGED",
    ]
    assert body["missing_side_effects"] == []
    assert body["extra_side_effects"] == []
    assert body["parity_summary"].startswith("Hard MVP demo heuristic")
    assert body["trusted_tool_candidate"] is True
    assert isinstance(body["requires_registration_approval"], bool)


def test_validation_gate_can_simulate_failed_parity(monkeypatch, tmp_path):
    response = TestClient(load_app(monkeypatch, tmp_path)).post(
        "/validate/request-purchase-order-approval",
        json={"simulate_failure": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract_test"] == "passed"
    assert body["business_rule_test"] == "passed"
    assert body["rpa_api_parity_check"] == "failed"
    assert body["parity_failure_reason"] == (
        "Simulated mismatch in audit log creation for demo failure path."
    )
    assert body["missing_side_effects"] == ["AUDIT_LOG_CREATED"]
    assert body["extra_side_effects"] == []
    assert body["api_result"]["audit_log_created"] is False
    assert body["parity_summary"].startswith("Hard MVP demo heuristic")
    assert body["trusted_tool_candidate"] is False
    assert body["requires_registration_approval"] is False
    assert body["recommended_recovery"] == (
        "Keep execution mode as RPA, generate fix task, require IT review, "
        "and rerun validation."
    )


def test_validation_passed_writes_validation_event_and_capabilities(monkeypatch, tmp_path):
    response = TestClient(load_app(monkeypatch, tmp_path)).post(
        "/validate/request-purchase-order-approval",
        json={"case_id": "CASE-001", "correlation_id": "corr-test-validation"},
    )

    assert response.status_code == 200
    from shared.automation_memory.event_types import MemoryEventType
    from shared.automation_memory.repository import (
        find_capability,
        query_capabilities,
        query_case_timeline,
    )

    events = query_case_timeline("CASE-001", data_dir=tmp_path)
    validation_events = [
        event
        for event in events
        if event.event_type == MemoryEventType.VALIDATION_COMPLETED
    ]
    assert len(validation_events) == 1
    event = validation_events[0]
    assert event.source_service == "validation-suite"
    assert event.correlation_id == "corr-test-validation"
    assert event.payload["validation_status"] == "passed"
    assert event.payload["source_endpoint"] == (
        "/validate/request-purchase-order-approval"
    )

    capabilities = query_capabilities(data_dir=tmp_path)
    assert len(capabilities) == 2
    trusted_api = find_capability(
        "request_purchase_order_approval",
        data_dir=tmp_path,
    )
    assert trusted_api is not None
    assert trusted_api.capability_id == "cap_api_request_po_approval_v1"
    assert trusted_api.status == "trusted"
    assert trusted_api.validation_status == "passed"


def test_validation_failed_writes_event_but_does_not_register_capability(
    monkeypatch,
    tmp_path,
):
    response = TestClient(load_app(monkeypatch, tmp_path)).post(
        "/validate/request-purchase-order-approval",
        json={
            "case_id": "CASE-001",
            "correlation_id": "corr-test-failed",
            "simulate_failure": True,
        },
    )

    assert response.status_code == 200
    from shared.automation_memory.event_types import MemoryEventType
    from shared.automation_memory.repository import (
        find_capability,
        query_case_timeline,
    )

    events = query_case_timeline("CASE-001", data_dir=tmp_path)
    validation_events = [
        event
        for event in events
        if event.event_type == MemoryEventType.VALIDATION_COMPLETED
    ]
    assert len(validation_events) == 1
    event = validation_events[0]
    assert event.payload["validation_status"] == "failed"
    assert event.payload["failure_reason"] == (
        "Simulated mismatch in audit log creation for demo failure path."
    )
    assert find_capability(
        "request_purchase_order_approval",
        data_dir=tmp_path,
    ) is None


def test_capability_gap_endpoint_writes_shared_memory_and_legacy_artifact(
    monkeypatch,
    tmp_path,
):
    response = TestClient(load_app(monkeypatch, tmp_path)).post(
        "/capability-gaps/inventory-shortage"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "CASE-003"
    assert body["coverage_status"] == "not_covered"

    from shared.automation_memory.repository import query_gaps

    gaps = query_gaps(data_dir=tmp_path)
    assert len(gaps) == 1
    assert gaps[0].event_type == "CAPABILITY_GAP_RECORDED"
    assert gaps[0].source_service == "validation-suite"
    assert gaps[0].payload["business_action"] == "resolve_inventory_shortage"
    assert gaps[0].payload["gap_status"] == "open"
    assert gaps[0].payload["priority"] == "medium"

    legacy_path = Path(__file__).resolve().parents[2] / "memory" / "data" / (
        "capability_gap_CASE-003.json"
    )
    assert legacy_path.exists()


def test_validation_memory_write_failure_does_not_block_endpoint(monkeypatch, tmp_path):
    app = load_app(monkeypatch, tmp_path)
    import app.main as main

    def fail_record_validation_result(*_args, **_kwargs):
        raise RuntimeError("memory unavailable")

    monkeypatch.setattr(main, "record_validation_result", fail_record_validation_result)

    response = TestClient(app).post("/validate/request-purchase-order-approval")

    assert response.status_code == 200
    assert response.json()["rpa_api_parity_check"] == "passed"


def test_gap_memory_write_failure_does_not_block_endpoint(monkeypatch, tmp_path):
    app = load_app(monkeypatch, tmp_path)
    import app.main as main

    def fail_record_capability_gap(*_args, **_kwargs):
        raise RuntimeError("memory unavailable")

    monkeypatch.setattr(main, "record_capability_gap", fail_record_capability_gap)

    response = TestClient(app).post("/capability-gaps/inventory-shortage")

    assert response.status_code == 200
    assert response.json()["coverage_status"] == "not_covered"


def test_memory_case_summary_without_data_does_not_500(monkeypatch, tmp_path):
    response = TestClient(load_app(monkeypatch, tmp_path)).get(
        "/memory/cases/CASE-MISSING"
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "case_id": "CASE-MISSING",
        "event_count": 0,
        "latest_event_type": None,
        "latest_event_at": None,
        "empty": True,
        "timeline": [],
    }


def test_memory_timeline_query_returns_events_in_order(monkeypatch, tmp_path):
    from shared.automation_memory.event_types import MemoryEventType
    from shared.automation_memory.repository import record_case_event

    record_case_event(
        "CASE-001",
        MemoryEventType.TRIAGE_COMPLETED,
        {"step": "triage"},
        source_service="pytest",
        data_dir=tmp_path,
    )
    record_case_event(
        "CASE-001",
        MemoryEventType.VALIDATION_COMPLETED,
        {"step": "validation"},
        source_service="pytest",
        data_dir=tmp_path,
    )

    response = TestClient(load_app(monkeypatch, tmp_path)).get(
        "/memory/cases/CASE-001/timeline"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "CASE-001"
    assert [event["event_type"] for event in body["events"]] == [
        "TRIAGE_COMPLETED",
        "VALIDATION_COMPLETED",
    ]
    created_at = [event["created_at"] for event in body["events"]]
    assert created_at == sorted(created_at)


def test_memory_decisions_query_returns_agent_decision_events(monkeypatch, tmp_path):
    from shared.automation_memory.repository import record_agent_decision

    record_agent_decision(
        "CASE-001",
        {"detected_exception_type": "budget_exceeded"},
        data_dir=tmp_path,
    )

    response = TestClient(load_app(monkeypatch, tmp_path)).get(
        "/memory/decisions/CASE-001"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["empty"] is False
    assert body["events"][0]["event_type"] == "TRIAGE_COMPLETED"


def test_memory_capabilities_query_returns_list(monkeypatch, tmp_path):
    from shared.automation_memory.repository import register_capability

    register_capability(
        {
            "capability_id": "cap_api_request_po_approval_v1",
            "business_action": "request_purchase_order_approval",
            "capability_type": "api",
            "execution_mode": "API",
            "endpoint": "http://localhost:8003/api/purchase-orders/{po_id}/approval-request",
            "status": "trusted",
            "validation_status": "passed",
        },
        data_dir=tmp_path,
    )

    response = TestClient(load_app(monkeypatch, tmp_path)).get("/memory/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["capabilities"], list)
    assert body["capabilities"][0]["capability_id"] == (
        "cap_api_request_po_approval_v1"
    )


def test_memory_gaps_query_returns_gap_events(monkeypatch, tmp_path):
    from shared.automation_memory.repository import record_capability_gap

    record_capability_gap(
        "CASE-003",
        {
            "business_action": "resolve_inventory_shortage",
            "gap_type": "missing_trusted_capability",
            "gap_status": "open",
        },
        data_dir=tmp_path,
    )

    response = TestClient(load_app(monkeypatch, tmp_path)).get("/memory/gaps")

    assert response.status_code == 200
    body = response.json()
    assert len(body["gaps"]) == 1
    assert body["gaps"][0]["event_type"] == "CAPABILITY_GAP_RECORDED"


def test_memory_query_api_does_not_affect_validation_endpoint(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, tmp_path))

    timeline = client.get("/memory/cases/CASE-001/timeline")
    validation = client.post("/validate/request-purchase-order-approval")

    assert timeline.status_code == 200
    assert validation.status_code == 200
    assert validation.json()["rpa_api_parity_check"] == "passed"
