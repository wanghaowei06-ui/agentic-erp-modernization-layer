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


def test_purchase_order_list_contains_seed_data():
    with TestClient(load_app()) as client:
        response = client.get("/purchase-orders")
        detail = client.get("/purchase-orders/PO-1001")

    assert response.status_code == 200
    assert "PO-1001" in response.text
    assert "PO-1002" in response.text
    assert detail.status_code == 200
    assert 'id="ctl00_MainContent_lblPoNumber"' in detail.text
    assert 'id="ctl00_MainContent_lblAmount"' in detail.text
    assert 'id="ctl00_MainContent_btnRequestApproval"' in detail.text


def test_rpa_writeback_form_updates_status(monkeypatch, tmp_path):
    with TestClient(load_app(monkeypatch, tmp_path)) as client:
        response = client.post(
            "/purchase-orders/PO-1001/request-approval",
            data={
                "approval_reason": "Amount exceeds budget limit",
                "manager_id": "MGR-001",
            },
    )

    assert response.status_code == 200
    assert 'id="writeback-status" hidden>PENDING_MANAGER_APPROVAL' in response.text
    assert 'id="ctl00_MainContent_lblWritebackStatus"' in response.text
    assert 'id="writeback-execution-mode" hidden>RPA' in response.text
    assert "PO_STATUS_UPDATED" in response.text
    assert "rpa-trace-PO-1001" in response.text


def test_rpa_writeback_records_memory_event(monkeypatch, tmp_path):
    with TestClient(load_app(monkeypatch, tmp_path)) as client:
        response = client.post(
            "/purchase-orders/PO-1001/request-approval",
            data={
                "case_id": "CASE-RPA-001",
                "correlation_id": "corr-rpa-test",
                "approval_reason": "Amount exceeds budget limit",
                "manager_id": "MGR-001",
            },
        )

    assert response.status_code == 200
    from shared.automation_memory.event_types import MemoryEventType
    from shared.automation_memory.repository import query_case_timeline

    events = query_case_timeline("CASE-RPA-001", data_dir=tmp_path)
    rpa_events = [
        event
        for event in events
        if event.event_type == MemoryEventType.RPA_WRITEBACK_COMPLETED
    ]
    assert len(rpa_events) == 1
    event = rpa_events[0]
    assert event.source_service == "mock-legacy-erp"
    assert event.correlation_id == "corr-rpa-test"
    assert event.payload["po_id"] == "PO-1001"
    assert event.payload["business_action"] == "request_purchase_order_approval"
    assert event.payload["execution_mode"] == "RPA"
    assert event.payload["status"] == "PENDING_MANAGER_APPROVAL"
    assert event.payload["audit_log_created"] is True
    assert event.payload["before_state"]["status"] == "Exception"
    assert event.payload["after_state"] == {
        "status": "PENDING_MANAGER_APPROVAL",
        "audit_log_created": True,
    }


def test_rpa_writeback_memory_case_id_falls_back_to_po_id(monkeypatch, tmp_path):
    with TestClient(load_app(monkeypatch, tmp_path)) as client:
        response = client.post(
            "/purchase-orders/PO-1002/request-approval",
            data={
                "approval_reason": "Vendor exception",
                "manager_id": "MGR-001",
            },
        )

    assert response.status_code == 200
    from shared.automation_memory.repository import query_case_timeline

    events = query_case_timeline("CASE-PO-1002", data_dir=tmp_path)
    assert len(events) == 1
    assert events[0].payload["po_id"] == "PO-1002"


def test_rpa_writeback_memory_failure_does_not_block_response(monkeypatch, tmp_path):
    app = load_app(monkeypatch, tmp_path)
    import app.main as main

    def fail_record_execution_trace(*_args, **_kwargs):
        raise RuntimeError("memory unavailable")

    monkeypatch.setattr(main, "record_execution_trace", fail_record_execution_trace)
    with TestClient(app) as client:
        response = client.post(
            "/purchase-orders/PO-1001/request-approval",
            data={
                "approval_reason": "Amount exceeds budget limit",
                "manager_id": "MGR-001",
            },
        )

    assert response.status_code == 200
    assert 'id="writeback-status" hidden>PENDING_MANAGER_APPROVAL' in response.text


def test_enhanced_demo_pages_render_stable_ids():
    with TestClient(load_app()) as client:
        dashboard = client.get("/case-dashboard")
        timeline = client.get("/case-timeline/CASE-001")
        scorecard = client.get("/api-readiness-scorecard")
        registry = client.get("/tool-registry")

    assert dashboard.status_code == 200
    assert 'id="case-dashboard"' in dashboard.text
    assert 'id="case-001-card"' in dashboard.text
    assert 'id="case-002-card"' in dashboard.text
    assert 'id="case-003-card"' in dashboard.text
    assert timeline.status_code == 200
    assert 'id="case-timeline"' in timeline.text
    assert 'id="timeline-api-mode-executed"' in timeline.text
    assert scorecard.status_code == 200
    assert 'id="api-readiness-scorecard"' in scorecard.text
    assert 'id="readiness-final-score">86' in scorecard.text
    assert registry.status_code == 200
    assert 'id="tool-registry"' in registry.text
    assert 'id="tool-request-po-approval-status">registered' in registry.text


def test_enhanced_demo_json_endpoints():
    with TestClient(load_app()) as client:
        timeline = client.get("/api/demo/cases/CASE-001/timeline")
        scorecard = client.get("/api/demo/api-readiness-scorecard")
        registry = client.get("/api/demo/tool-registry")
        reset = client.post("/api/demo/reset")

    assert timeline.status_code == 200
    assert timeline.json()["timeline"][-1]["event"] == "API Mode Executed"
    assert scorecard.status_code == 200
    assert scorecard.json()["final_score"] == 86
    assert scorecard.json()["risk_level"] == "high"
    assert registry.status_code == 200
    assert registry.json()["governance_owner"] == "UiPath"
    assert reset.status_code == 200
    assert reset.json()["status"] == "reset"


def test_full_competition_case_selection_and_tool_registration():
    with TestClient(load_app()) as client:
        cases = client.get("/api/demo/cases")
        next_case = client.get("/api/demo/cases/next?strategy=modernization_value")
        task = client.post(
            "/api/demo/modernization/tasks",
            json={
                "case_id": "CASE-001",
                "plan_id": "MOD-PLAN-001",
                "tool_name": "request_purchase_order_approval",
                "business_action": "request_purchase_order_approval",
                "approval_status": "APPROVED_BY_AUTOMATION_OWNER",
                "approved_by": "automation_owner",
                "target_service": "generated-api-facade",
                "proposed_endpoint": (
                    "POST /api/purchase-orders/{po_id}/approval-request"
                ),
            },
        )
        registration = client.post(
            "/api/demo/tool-registry/register",
            json={
                "case_id": "CASE-001",
                "tool_name": "request_purchase_order_approval",
                "business_action": "request_purchase_order_approval",
                "approval_status": "APPROVED",
                "approved_by": "automation_owner",
                "validation_status": "VALIDATION_PASSED",
                "readiness_score": 0.91,
                "modernization_task_id": "MOD-TASK-001",
                "source_execution_mode": "RPA",
                "target_execution_mode": "API",
                "api_endpoint": (
                    "http://localhost:8003/api/purchase-orders/"
                    "PO-1001/approval-request"
                ),
            },
        )
        rpa_trace = client.get("/api/demo/rpa-traces/PO-1001")
        registry = client.get("/api/demo/tool-registry")

    assert cases.status_code == 200
    assert len(cases.json()["cases"]) == 3
    assert next_case.status_code == 200
    assert next_case.json()["selected_case_id"] == "CASE-001"
    assert task.status_code == 200
    assert task.json()["status"] == "READY_FOR_CODEX_DRAFT_PR"
    assert registration.status_code == 200
    assert registration.json()["trusted_tool_registered"] is True
    assert rpa_trace.status_code == 200
    assert rpa_trace.json()["side_effects"] == []
    assert registry.status_code == 200
    assert registry.json()["registered_tools"][0]["tool_name"] == (
        "request_purchase_order_approval"
    )
