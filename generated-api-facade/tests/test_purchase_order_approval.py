import sys
from pathlib import Path

from fastapi.testclient import TestClient


def load_service():
    service_root = Path(__file__).resolve().parents[1]
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    sys.path.insert(0, str(service_root))
    from app.db import audit_count
    from app.main import app

    return app, audit_count


def test_purchase_order_approval_returns_api_execution_mode():
    app, _ = load_service()
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
    }


def test_purchase_order_approval_is_idempotent():
    app, audit_count = load_service()
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
