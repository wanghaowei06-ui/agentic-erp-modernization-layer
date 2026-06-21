import sys
from pathlib import Path

from fastapi.testclient import TestClient


def load_app():
    service_root = Path(__file__).resolve().parents[1]
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    sys.path.insert(0, str(service_root))
    from app.main import app

    return app


def test_purchase_order_list_contains_seed_data():
    with TestClient(load_app()) as client:
        response = client.get("/purchase-orders")

    assert response.status_code == 200
    assert "PO-1001" in response.text
    assert "PO-1002" in response.text


def test_rpa_writeback_form_updates_status():
    with TestClient(load_app()) as client:
        response = client.post(
            "/purchase-orders/PO-1001/request-approval",
            data={
                "approval_reason": "Amount exceeds budget limit",
                "manager_id": "MGR-001",
            },
        )

    assert response.status_code == 200
    assert 'id="writeback-status">PENDING_MANAGER_APPROVAL' in response.text
    assert 'id="writeback-execution-mode">RPA' in response.text


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
