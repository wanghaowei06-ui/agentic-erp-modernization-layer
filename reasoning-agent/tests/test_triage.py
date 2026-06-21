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


def test_po_1001_budget_exceeded():
    payload = {
        "case_id": "CASE-001",
        "po_id": "PO-1001",
        "amount": 18000,
        "budget_limit": 10000,
        "vendor_id": "V-203",
        "vendor_info_complete": True,
        "inventory_available": True,
        "erp_status": "Exception",
        "raw_exception_text": "Amount exceeds approved budget limit",
    }

    response = TestClient(load_app()).post("/triage", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["detected_exception_type"] == "budget_exceeded"
    assert body["risk_level"] == "high"
    assert body["requires_human_approval"] is True
    assert body["next_stage"] == "WAITING_FOR_HUMAN_APPROVAL"


def test_po_1002_vendor_info_missing():
    payload = {
        "case_id": "CASE-002",
        "po_id": "PO-1002",
        "amount": 6000,
        "budget_limit": 10000,
        "vendor_id": None,
        "vendor_info_complete": False,
        "inventory_available": True,
        "erp_status": "Exception",
        "raw_exception_text": "Vendor information missing",
    }

    response = TestClient(load_app()).post("/triage", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["detected_exception_type"] == "vendor_info_missing"
    assert body["risk_level"] == "medium"
    assert body["requires_human_approval"] is False
    assert body["next_stage"] == "WAITING_VENDOR_INFO"
