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


def test_validation_gate_returns_passed_result():
    response = TestClient(load_app()).post("/validate/request-purchase-order-approval")

    assert response.status_code == 200
    body = response.json()
    assert body["data_isolation"] == "cloned_test_cases"
    assert body["rpa_test_case_id"] == "PO-1001-RPA"
    assert body["api_test_case_id"] == "PO-1001-API"
    assert body["contract_test"] == "passed"
    assert body["business_rule_test"] == "passed"
    assert body["rpa_api_parity_check"] == "passed"
    assert body["trusted_tool_candidate"] is True


def test_validation_gate_can_simulate_failed_parity():
    response = TestClient(load_app()).post(
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
    assert body["trusted_tool_candidate"] is False
    assert body["requires_registration_approval"] is False
    assert body["recommended_recovery"] == (
        "Keep execution mode as RPA, generate fix task, require IT review, "
        "and rerun validation."
    )
