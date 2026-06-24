import sys
from pathlib import Path

from fastapi.testclient import TestClient


def load_app(monkeypatch, *, demo_mode="mock_success", api_key=None, memory_dir=None):
    service_root = Path(__file__).resolve().parents[1]
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("SKIP_DOTENV_LOAD", "1")
    if demo_mode is None:
        monkeypatch.delenv("LLM_DEMO_MODE", raising=False)
    else:
        monkeypatch.setenv("LLM_DEMO_MODE", demo_mode)
    if api_key is None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
    else:
        monkeypatch.setenv("LLM_API_KEY", api_key)
    if memory_dir is not None:
        monkeypatch.setenv("AUTOMATION_MEMORY_DIR", str(memory_dir))
    else:
        monkeypatch.delenv("AUTOMATION_MEMORY_DIR", raising=False)
    sys.path.insert(0, str(service_root))
    from app.main import app

    return app


def po_1001_payload():
    return {
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


def readiness_payload(**overrides):
    payload = {
        "case_id": "CASE-001",
        "po_id": "PO-1001",
        "business_action": "request_purchase_order_approval",
        "detected_exception_type": "budget_exceeded",
        "frequency_30d": 42,
        "rpa_writeback_status": "PENDING_MANAGER_APPROVAL",
        "validation_status": "VALIDATION_PASSED",
        "human_approval": "APPROVED",
        "side_effects_observed": True,
        "rpa_api_parity_required": True,
        "rpa_api_parity_check": "passed",
    }
    payload.update(overrides)
    return payload


def test_mock_llm_success_po_1001_budget_exceeded(monkeypatch, tmp_path):
    response = TestClient(load_app(monkeypatch, memory_dir=tmp_path)).post(
        "/triage", json=po_1001_payload()
    )

    assert response.status_code == 200
    body = response.json()
    assert body["detected_exception_type"] == "budget_exceeded"
    assert body["risk_level"] == "high"
    assert body["requires_human_approval"] is True
    assert body["next_stage"] == "WAITING_FOR_HUMAN_APPROVAL"
    assert body["next_action"] == "request_manager_approval"
    assert body["fallback"] == "manual_investigation"
    assert body["evidence"] == [
        {"field": "amount", "value": 18000, "source": "legacy_erp_screen"},
        {"field": "budget_limit", "value": 10000, "source": "legacy_erp_screen"},
    ]
    assert body["decision_source"] == "deterministic_rule"
    assert body["business_action"] == "request_purchase_order_approval"
    assert body["required_approval_type"] == "manager_approval"
    assert body["recommended_next_stage"] == "WAITING_FOR_HUMAN_APPROVAL"
    assert body["capability_lookup_required"] is True
    assert body["guardrail_status"] == "passed"
    assert body["schema_version"] == "1.0"
    assert body["correlation_id"].startswith("corr_")
    assert body["memory_references"][0]["event_type"] == "TRIAGE_COMPLETED"


def test_mock_llm_success_po_1002_vendor_info_missing(monkeypatch, tmp_path):
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

    response = TestClient(load_app(monkeypatch, memory_dir=tmp_path)).post(
        "/triage", json=payload
    )

    assert response.status_code == 200
    body = response.json()
    assert body["detected_exception_type"] == "vendor_info_missing"
    assert body["risk_level"] == "medium"
    assert body["requires_human_approval"] is False
    assert body["next_stage"] == "WAITING_VENDOR_INFO"
    assert body["business_action"] == "collect_vendor_information"
    assert body["required_approval_type"] == "data_owner_review"


def test_no_api_key_fails_closed(monkeypatch, tmp_path):
    response = TestClient(load_app(monkeypatch, demo_mode=None, memory_dir=tmp_path)).post(
        "/triage",
        json=po_1001_payload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["detected_exception_type"] == "budget_exceeded"
    assert body["next_stage"] == "WAITING_FOR_HUMAN_APPROVAL"


def test_invalid_llm_json_retries_then_fails_closed(monkeypatch, tmp_path):
    app = load_app(
        monkeypatch,
        demo_mode=None,
        api_key="test-key",
        memory_dir=tmp_path,
    )
    import app.main as main

    def invalid_json(_prompt, _config, _agent_name, _request_id):
        return "not-json"

    monkeypatch.setattr(main, "call_llm_json", invalid_json)
    response = TestClient(app).post("/triage", json=po_1001_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["detected_exception_type"] == "budget_exceeded"
    assert body["next_stage"] == "WAITING_FOR_HUMAN_APPROVAL"


def test_triage_writes_po_1001_agent_decision_memory(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, memory_dir=tmp_path))

    response = client.post("/triage", json=po_1001_payload())

    assert response.status_code == 200
    from shared.automation_memory.event_types import MemoryEventType
    from shared.automation_memory.repository import query_case_decisions

    decisions = query_case_decisions("CASE-001", data_dir=tmp_path)
    assert len(decisions) == 1
    event = decisions[0]
    assert event.event_type == MemoryEventType.TRIAGE_COMPLETED
    assert event.source_service == "reasoning-agent"
    assert event.payload["po_id"] == "PO-1001"
    assert event.payload["detected_exception_type"] == "budget_exceeded"
    assert event.payload["business_action"] == "request_purchase_order_approval"
    assert event.payload["required_approval_type"] == "manager_approval"
    assert event.payload["guardrail_status"] == "passed"


def test_triage_writes_po_1002_vendor_decision_memory(monkeypatch, tmp_path):
    payload = {
        "case_id": "CASE-002",
        "po_id": "PO-1002",
        "amount": 6000,
        "budget_limit": 10000,
        "vendor_id": "",
        "vendor_info_complete": False,
        "inventory_available": True,
        "erp_status": "Exception",
        "raw_exception_text": "Vendor information missing",
    }
    client = TestClient(load_app(monkeypatch, memory_dir=tmp_path))

    response = client.post("/triage", json=payload)

    assert response.status_code == 200
    from shared.automation_memory.repository import query_case_decisions

    decisions = query_case_decisions("CASE-002", data_dir=tmp_path)
    assert len(decisions) == 1
    assert decisions[0].payload["detected_exception_type"] == "vendor_info_missing"
    assert decisions[0].payload["business_action"] == "collect_vendor_information"


def test_triage_writes_po_1003_inventory_decision_memory(monkeypatch, tmp_path):
    payload = {
        "case_id": "CASE-003",
        "po_id": "PO-1003",
        "amount": 8500,
        "budget_limit": 10000,
        "vendor_id": "V-118",
        "vendor_info_complete": True,
        "inventory_available": False,
        "erp_status": "Exception",
        "raw_exception_text": "Inventory shortage",
    }
    client = TestClient(load_app(monkeypatch, memory_dir=tmp_path))

    response = client.post("/triage", json=payload)

    assert response.status_code == 200
    from shared.automation_memory.repository import query_case_decisions

    decisions = query_case_decisions("CASE-003", data_dir=tmp_path)
    assert len(decisions) == 1
    assert decisions[0].payload["detected_exception_type"] == "inventory_shortage"
    assert decisions[0].payload["business_action"] == "resolve_inventory_shortage"
    assert decisions[0].payload["recommended_next_stage"] == "CAPABILITY_GAP_DETECTED"


def test_triage_memory_write_failure_does_not_block_response(monkeypatch, tmp_path):
    app = load_app(monkeypatch, memory_dir=tmp_path)
    import app.main as main

    def fail_record_agent_decision(*_args, **_kwargs):
        raise RuntimeError("memory unavailable")

    monkeypatch.setattr(main, "record_agent_decision", fail_record_agent_decision)

    response = TestClient(app).post("/triage", json=po_1001_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["detected_exception_type"] == "budget_exceeded"
    assert body["memory_references"] == []


def test_readiness_success_uses_llm_and_guardrails(monkeypatch):
    response = TestClient(load_app(monkeypatch)).post(
        "/modernization/readiness",
        json=readiness_payload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["modernization_candidate"] is True
    assert body["llm_call_mode"] == "mock"
    assert body["llm_invocation_verified"] is True
    assert body["readiness_score"] == 0.91
    assert body["recommended_next_stage"] == "CREATE_MODERNIZATION_PLAN"
    assert body["blocking_reasons"] == []
    assert body["side_effects_observed"] is True
    assert body["rpa_api_parity_required"] is True
    assert body["reasoning_mode"] == "llm_backed"


def test_readiness_llm_true_but_guardrail_fails_final_false(monkeypatch):
    response = TestClient(load_app(monkeypatch)).post(
        "/modernization/readiness",
        json=readiness_payload(
            frequency_30d=4,
            rpa_api_parity_check="failed",
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["modernization_candidate"] is False
    assert body["readiness_score"] < 0.6
    assert body["decision_status"] == "GUARDRAIL_BLOCKED"
    assert body["llm_call_mode"] == "mock"
    assert body["llm_invocation_verified"] is False
    assert "frequency_30d below modernization threshold" in body["blocking_reasons"]
    assert "RPA/API parity check did not pass" in body["blocking_reasons"]


def test_readiness_no_api_key_fails_closed(monkeypatch):
    response = TestClient(load_app(monkeypatch, demo_mode=None)).post(
        "/modernization/readiness",
        json=readiness_payload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["modernization_candidate"] is False
    assert body["llm_enabled"] is False
    assert body["decision_status"] == "MODEL_UNAVAILABLE"
    assert body["llm_invocation_verified"] is False
    assert body["recommended_next_stage"] == "WAITING_MANUAL_REVIEW"


def test_modernization_plan_success(monkeypatch):
    response = TestClient(load_app(monkeypatch)).post(
        "/modernization/plan",
        json={
            "case_id": "CASE-001",
            "business_action": "request_purchase_order_approval",
            "modernization_candidate": True,
            "recommended_api_tool_name": "request_purchase_order_approval",
            "readiness_score": 0.91,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"] == "MOD-PLAN-001"
    assert body["recommended_next_stage"] == "AUTOMATION_OWNER_PLAN_REVIEW"
    assert "rpa_api_parity_check" in body["tests_required"]
    assert body["rpa_api_parity_required"] is True
    assert "BUDGET_REVIEW_FLAGGED" in body["side_effects_signature"]
    assert body["schema_validated"] is True
    assert body["llm_call_mode"] == "mock"
    assert body["llm_invocation_verified"] is True


def test_modernization_plan_invalid_output_fails_closed(monkeypatch):
    app = load_app(monkeypatch, demo_mode=None, api_key="test-key")
    import app.main as main

    def invalid_plan(_prompt, _config, _agent_name, _request_id):
        return '{"plan_id": "MOD-PLAN-001"}'

    monkeypatch.setattr(main, "call_llm_json", invalid_plan)
    response = TestClient(app).post(
        "/modernization/plan",
        json={
            "case_id": "CASE-001",
            "business_action": "request_purchase_order_approval",
            "modernization_candidate": True,
            "recommended_api_tool_name": "request_purchase_order_approval",
            "readiness_score": 0.91,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["plan_id"] == "PLAN_GENERATION_FAILED"
    assert body["decision_status"] == "PLAN_GENERATION_FAILED"
    assert body["llm_invocation_verified"] is False
    assert body["recommended_next_stage"] == "WAITING_MANUAL_REVIEW"


def test_triage_does_not_call_real_llm(monkeypatch):
    app = load_app(monkeypatch, demo_mode=None, api_key="test-key")
    import app.main as main

    calls = []

    def fake_real_json(_prompt, config, agent_name, request_id):
        calls.append(
            {
                "base_url": config.base_url,
                "api_key": config.api_key,
                "agent_name": agent_name,
                "request_id": request_id,
            }
        )
        return (
            '{"detected_exception_type":"budget_exceeded",'
            '"risk_level":"high",'
            '"requires_human_approval":true,'
            '"next_stage":"WAITING_FOR_HUMAN_APPROVAL",'
            '"reasoning_summary":"Real client path test.",'
            '"confidence":0.93}'
        )

    monkeypatch.setattr(main, "call_llm_json", fake_real_json)
    response = TestClient(app).post("/triage", json=po_1001_payload())

    assert response.status_code == 200
    body = response.json()
    assert calls == []
    assert body["detected_exception_type"] == "budget_exceeded"
    assert body["next_action"] == "request_manager_approval"


def test_real_mode_readiness_and_plan_are_marked_real(monkeypatch):
    app = load_app(monkeypatch, demo_mode=None, api_key="test-key")
    import app.main as main

    calls = []

    def fake_real_json(_prompt, config, agent_name, request_id):
        calls.append(agent_name)
        if agent_name == "readiness":
            return (
                '{"modernization_candidate":true,'
                '"readiness_score":0.91,'
                '"recommended_api_tool_name":"request_purchase_order_approval",'
                '"recommended_next_stage":"CREATE_MODERNIZATION_PLAN",'
                '"reasoning_summary":"Real readiness path test.",'
                '"blocking_reasons":[]}'
            )
        if agent_name == "plan":
            return (
                '{"plan_id":"MOD-PLAN-001",'
                '"target_tool_name":"request_purchase_order_approval",'
                '"target_service":"generated-api-facade",'
                '"proposed_endpoint":"POST /api/purchase-orders/{po_id}/approval-request",'
                '"source_rpa_trace":"Real plan path test.",'
                '"contract_requirements":["must return po_id"],'
                '"tests_required":["contract_test","business_rule_test","rpa_api_parity_check"],'
                '"risk_level":"medium",'
                '"requires_engineer_approval":true,'
                '"recommended_next_stage":"AUTOMATION_OWNER_PLAN_REVIEW"}'
            )
        raise AssertionError(agent_name)

    monkeypatch.setattr(main, "call_llm_json", fake_real_json)
    client = TestClient(app)
    readiness = client.post("/modernization/readiness", json=readiness_payload())
    plan = client.post(
        "/modernization/plan",
        json={
            "case_id": "CASE-001",
            "business_action": "request_purchase_order_approval",
            "modernization_candidate": True,
            "recommended_api_tool_name": "request_purchase_order_approval",
            "readiness_score": 0.91,
        },
    )

    assert readiness.status_code == 200
    assert plan.status_code == 200
    assert calls == ["readiness", "plan"]
    assert readiness.json()["llm_call_mode"] == "real"
    assert readiness.json()["llm_invocation_verified"] is True
    assert plan.json()["llm_call_mode"] == "real"
    assert plan.json()["llm_invocation_verified"] is True
