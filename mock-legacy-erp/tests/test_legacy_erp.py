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
