import sys
from pathlib import Path

from fastapi.testclient import TestClient


def load_service(monkeypatch=None, memory_dir=None):
    service_root = Path(__file__).resolve().parents[1]
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    if monkeypatch is not None and memory_dir is not None:
        monkeypatch.setenv("AUTOMATION_MEMORY_DIR", str(memory_dir))
    elif monkeypatch is not None:
        monkeypatch.delenv("AUTOMATION_MEMORY_DIR", raising=False)
    sys.path.insert(0, str(service_root))
    from app.db import audit_count
    from app.main import app

    return app, audit_count


def test_purchase_order_approval_returns_api_execution_mode(monkeypatch, tmp_path):
    app, _ = load_service(monkeypatch, tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/purchase-orders/PO-1001/approval-request",
            json={
                "approval_reason": "Amount exceeds budget limit",
                "manager_id": "MGR-001",
                "source_case_id": "CASE-001",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "po_id": "PO-1001",
        "status": "PENDING_MANAGER_APPROVAL",
        "audit_log_created": True,
        "execution_mode": "API",
        "source_case_id": "CASE-001",
        "side_effects": [
            "PO_STATUS_UPDATED",
            "APPROVAL_TASK_CREATED",
            "AUDIT_LOG_CREATED",
            "MANAGER_NOTIFICATION_QUEUED",
            "BUDGET_REVIEW_FLAGGED",
        ],
        "event_trace_id": "api-trace-PO-1001",
    }


def test_purchase_order_approval_is_idempotent(monkeypatch, tmp_path):
    app, audit_count = load_service(monkeypatch, tmp_path)
    with TestClient(app) as client:
        client.post(
            "/api/purchase-orders/PO-1002/approval-request",
            json={
                "approval_reason": "Vendor exception",
                "manager_id": "MGR-001",
                "source_case_id": "CASE-002",
            },
        )
        first_count = audit_count("PO-1002")
        response = client.post(
            "/api/purchase-orders/PO-1002/approval-request",
            json={
                "approval_reason": "Vendor exception",
                "manager_id": "MGR-001",
                "source_case_id": "CASE-002",
            },
        )
        second_count = audit_count("PO-1002")

    assert response.status_code == 200
    assert second_count == first_count


def test_purchase_order_approval_writes_api_execution_memory(monkeypatch, tmp_path):
    app, _ = load_service(monkeypatch, tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/purchase-orders/PO-1001-API/approval-request",
            json={
                "case_id": "CASE-API-001",
                "correlation_id": "corr-api-test",
                "approval_reason": "Amount exceeds budget limit",
                "manager_id": "MGR-001",
                "source_case_id": "CASE-001",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["execution_mode"] == "API"

    from shared.automation_memory.event_types import MemoryEventType
    from shared.automation_memory.repository import query_case_timeline

    events = query_case_timeline("CASE-API-001", data_dir=tmp_path)
    api_events = [
        event
        for event in events
        if event.event_type == MemoryEventType.API_EXECUTION_COMPLETED
    ]
    assert len(api_events) == 1
    event = api_events[0]
    assert event.source_service == "generated-api-facade"
    assert event.correlation_id == "corr-api-test"
    assert event.payload["po_id"] == "PO-1001-API"
    assert event.payload["case_id"] == "CASE-API-001"
    assert event.payload["business_action"] == "request_purchase_order_approval"
    assert event.payload["execution_mode"] == "API"
    assert event.payload["status"] == "PENDING_MANAGER_APPROVAL"
    assert event.payload["audit_log_created"] is True
    assert event.payload["event_trace_id"] == "api-trace-PO-1001-API"


def test_purchase_order_approval_memory_case_id_falls_back_to_source_case_id(
    monkeypatch,
    tmp_path,
):
    app, _ = load_service(monkeypatch, tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/purchase-orders/PO-1001/approval-request",
            json={
                "approval_reason": "Amount exceeds budget limit",
                "manager_id": "MGR-001",
                "source_case_id": "CASE-SOURCE-001",
            },
        )

    assert response.status_code == 200
    from shared.automation_memory.repository import query_case_timeline

    events = query_case_timeline("CASE-SOURCE-001", data_dir=tmp_path)
    assert len(events) == 1
    assert events[0].payload["po_id"] == "PO-1001"


def test_purchase_order_approval_memory_write_failure_does_not_block_response(
    monkeypatch,
    tmp_path,
):
    app, _ = load_service(monkeypatch, tmp_path)
    import app.main as main

    def fail_record_execution_trace(*_args, **_kwargs):
        raise RuntimeError("memory unavailable")

    monkeypatch.setattr(main, "record_execution_trace", fail_record_execution_trace)
    with TestClient(app) as client:
        response = client.post(
            "/api/purchase-orders/PO-1001/approval-request",
            json={
                "approval_reason": "Amount exceeds budget limit",
                "manager_id": "MGR-001",
                "source_case_id": "CASE-001",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["po_id"] == "PO-1001"
    assert body["execution_mode"] == "API"
    assert body["status"] == "PENDING_MANAGER_APPROVAL"
