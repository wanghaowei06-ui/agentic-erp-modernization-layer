import json
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
    capability_ref = body["memory_references"][0]
    assert capability_ref["type"] == "capability_lookup"
    assert capability_ref["business_action"] == "request_purchase_order_approval"
    assert capability_ref["capability_found"] is False
    assert capability_ref["lookup_status"] == "completed"
    assert body["memory_references"][1]["event_type"] == "TRIAGE_COMPLETED"


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


def test_triage_capability_lookup_finds_registered_capability(monkeypatch, tmp_path):
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

    client = TestClient(load_app(monkeypatch, memory_dir=tmp_path))
    response = client.post("/triage", json=po_1001_payload())

    assert response.status_code == 200
    body = response.json()
    capability_refs = [
        ref for ref in body["memory_references"] if ref["type"] == "capability_lookup"
    ]
    assert len(capability_refs) == 1
    assert capability_refs[0]["capability_found"] is True
    assert capability_refs[0]["capability_id"] == "cap_api_request_po_approval_v1"
    assert capability_refs[0]["execution_mode"] == "API"
    assert capability_refs[0]["endpoint"] == (
        "http://localhost:8003/api/purchase-orders/{po_id}/approval-request"
    )
    assert capability_refs[0]["lookup_status"] == "completed"
    # TRIAGE_COMPLETED event reference is still appended after the lookup
    assert body["memory_references"][-1]["event_type"] == "TRIAGE_COMPLETED"


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
    # Capability lookup still runs in classify_exception even when the
    # triage memory write fails, but the TRIAGE_COMPLETED reference is
    # not appended because record_agent_decision raised.
    assert len(body["memory_references"]) == 1
    assert body["memory_references"][0]["type"] == "capability_lookup"
    assert body["memory_references"][0]["capability_found"] is False


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


def _record_inventory_gap(data_dir, case_id="CASE-003"):
    from shared.automation_memory.repository import record_capability_gap

    record_capability_gap(
        case_id,
        {
            "exception_type": "inventory_shortage",
            "required_business_action": "request_inventory_review",
            "coverage_status": "not_covered",
        },
        source_service="validation-suite",
        data_dir=data_dir,
    )


def test_capability_evolution_does_not_trigger_below_threshold(monkeypatch, tmp_path):
    _record_inventory_gap(tmp_path, "CASE-003")
    _record_inventory_gap(tmp_path, "CASE-004")

    client = TestClient(load_app(monkeypatch, memory_dir=tmp_path))
    response = client.post(
        "/capability-evolution/evaluate",
        json={"business_action": "request_inventory_review"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["triggered"] is False
    assert body["repeated_gaps"] == 2
    assert body["threshold"] == 3
    assert body.get("plan") is None


def test_capability_evolution_triggers_run_plan_agent_at_threshold(monkeypatch, tmp_path):
    for case_id in ("CASE-003", "CASE-004", "CASE-005"):
        _record_inventory_gap(tmp_path, case_id)

    client = TestClient(load_app(monkeypatch, memory_dir=tmp_path))
    response = client.post(
        "/capability-evolution/evaluate",
        json={
            "business_action": "request_inventory_review",
            "case_id": "CASE-003",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["triggered"] is True
    assert body["repeated_gaps"] == 3
    assert body["threshold"] == 3
    plan = body["plan"]
    assert plan is not None
    assert plan["case_id"] == "CASE-003"
    assert plan["target_tool_name"] == "request_inventory_review"


def test_capability_evolution_threshold_is_configurable(monkeypatch, tmp_path):
    _record_inventory_gap(tmp_path, "CASE-003")

    monkeypatch.setenv("CAPABILITY_EVOLUTION_THRESHOLD", "1")
    client = TestClient(load_app(monkeypatch, memory_dir=tmp_path))
    response = client.post(
        "/capability-evolution/evaluate",
        json={"business_action": "request_inventory_review"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["triggered"] is True
    assert body["threshold"] == 1
    assert body["repeated_gaps"] == 1


def test_capability_gap_proposal_matches_prd_18_4_format(monkeypatch):
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post(
        "/capability-gap/proposal",
        json={
            "case_id": "CASE-003",
            "po_id": "PO-1003",
            "detected_exception_type": "inventory_shortage",
            "required_business_action": "request_inventory_review",
            "available_capabilities": [
                "ReadPurchaseOrder.xaml",
                "request_purchase_order_approval_api",
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    # PRD 18.4 exact field set and values.
    assert body["case_id"] == "CASE-003"
    assert body["coverage_status"] == "not_covered"
    assert body["missing_capability"].startswith("No registered workflow")
    assert body["recommended_next_step"] == "create_new_workflow_proposal"
    assert body["proposed_workflow_name"] == "HandleInventoryShortageReview.xaml"
    assert body["human_approval_required"] is True
    assert body["current_case_resolution"] == "manual_handling_required"
    # run_plan_agent supplementary fields are populated.
    assert body["plan_id"] == "MOD-PLAN-001"
    assert body["target_tool_name"] == "request_inventory_review"
    assert body["proposed_endpoint"] == "POST /api/purchase-orders/{po_id}/approval-request"
    assert "contract_test" in body["tests_required"]


def test_capability_gap_proposal_uses_defaults_for_po_1003(monkeypatch):
    """POSTing an empty body must default to the PO-1003 inventory gap."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post("/capability-gap/proposal", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "CASE-003"
    assert body["proposed_workflow_name"] == "HandleInventoryShortageReview.xaml"


def test_langgraph_memory_saver_persists_case_agent_state(monkeypatch):
    """PRD 17.5 Enhanced: invoking plan agent persists state under case_id."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    # No checkpoint before the plan agent runs.
    before = client.get("/agent-state/CASE-PLAN-001")
    assert before.status_code == 404

    plan = client.post(
        "/modernization/plan",
        json={
            "case_id": "CASE-PLAN-001",
            "business_action": "request_purchase_order_approval",
            "modernization_candidate": True,
            "recommended_api_tool_name": "request_purchase_order_approval",
        },
    )
    assert plan.status_code == 200

    # After the plan agent runs the checkpoint exists and is keyed by case_id.
    after = client.get("/agent-state/CASE-PLAN-001")
    assert after.status_code == 200
    body = after.json()
    assert body["case_id"] == "CASE-PLAN-001"
    assert body["thread_id"] == "CASE-PLAN-001"
    assert body["checkpoint_id"]
    assert "agent_state" in body


def test_langgraph_memory_saver_missing_checkpoint_returns_404(monkeypatch):
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.get("/agent-state/CASE-NO-CHECKPOINT")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "no_checkpoint"


def test_capability_registry_check_returns_trusted_capability(monkeypatch):
    """PO-1001 follow-up run: registry has trusted API → skip re-modernization."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post(
        "/capability-registry/check",
        json={
            "business_action": "request_purchase_order_approval",
            "exception_type": "budget_exceeded",
            "case_id": "CASE-001",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["capability_found"] is True
    assert body["capability_id"] == "request_purchase_order_approval_api"
    assert body["capability_type"] == "API_TOOL"
    assert body["execution_mode"] == "API"
    assert body["modernization_required"] is False
    assert body["next_stage"] == "API_MODE_EXECUTION"


def test_capability_registry_check_routes_to_evolution_when_not_found(monkeypatch):
    """No trusted capability → route to capability-evolution evaluation."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post(
        "/capability-registry/check",
        json={
            "business_action": "request_inventory_review",
            "exception_type": "inventory_shortage",
            "case_id": "CASE-003",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["capability_found"] is False
    assert body["modernization_required"] is True
    assert body["next_stage"] == "CAPABILITY_EVOLUTION_EVALUATION"
    # exclude_none=True so absent optional fields are not serialised
    assert "capability_id" not in body
    assert "execution_mode" not in body


def test_capability_evolution_evaluate_uses_trusted_capability(monkeypatch):
    """CASE-001 follow-up: registry trusted → USE_TRUSTED_CAPABILITY."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post(
        "/capability-evolution/decision",
        json={
            "case_id": "CASE-001",
            "po_id": "PO-1001",
            "exception_type": "budget_exceeded",
            "business_action": "request_purchase_order_approval",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "USE_TRUSTED_CAPABILITY"
    assert body["capability_id"] == "request_purchase_order_approval_api"
    assert body["execution_mode"] == "API"
    assert body["modernization_required"] is False


def test_capability_evolution_evaluate_xaml_workflow_proposal(monkeypatch):
    """CASE-003: repeated gap, no trusted capability → XAML_WORKFLOW_PROPOSAL."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post(
        "/capability-evolution/decision",
        json={
            "case_id": "CASE-003",
            "po_id": "PO-1003",
            "exception_type": "inventory_shortage",
            "business_action": "request_inventory_review",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "XAML_WORKFLOW_PROPOSAL"
    assert "HandleInventoryShortageReview.xaml" in body["recommended_change"]
    assert body["requires_human_approval"] is True
    assert body["coding_agent_allowed"] == "after_approval_only"


def test_capability_evolution_evaluate_wait_for_vendor_info(monkeypatch):
    """vendor_info_missing → WAIT_FOR_VENDOR_INFO (business data waiting)."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post(
        "/capability-evolution/decision",
        json={
            "case_id": "CASE-002",
            "po_id": "PO-1002",
            "exception_type": "vendor_info_missing",
            "business_action": "handle_vendor_info_missing",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "WAIT_FOR_VENDOR_INFO"
    assert body["next_stage"] == "WAITING_VENDOR_INFO"
    assert body["api_modernization_required"] is False
    assert body["status"] == "WAITING_BUSINESS_DATA"
    assert "Vendor information is missing" in body["reason"]


def test_case_dashboard_renders_html_for_case_001(monkeypatch):
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.get("/case-dashboard/CASE-001")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    body = response.text
    assert "CASE-001" in body
    assert "Case Overview" in body
    assert "Timeline" in body
    assert "API_MODE_EXECUTED" in body
    assert "Decision Panel" in body
    assert "Validation / Readiness Panel" in body
    assert "Memory Evidence Panel" in body
    assert "Capability Registry / Gap Panel" in body
    assert "request_purchase_order_approval_api" in body


def test_case_dashboard_renders_case_002_and_003(monkeypatch):
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    for case_id, expected_marker in [
        ("CASE-002", "WAITING_VENDOR_INFO"),
        ("CASE-003", "HandleInventoryShortageReview.xaml"),
    ]:
        response = client.get(f"/case-dashboard/{case_id}")
        assert response.status_code == 200
        assert expected_marker in response.text


def test_case_dashboard_returns_404_for_unknown_case(monkeypatch):
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.get("/case-dashboard/CASE-999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Task 4: Capability Evolution Decision explainability fields
# ---------------------------------------------------------------------------

def test_capability_evolution_decision_has_explainability_for_api_modernization(monkeypatch):
    """API_MODERNIZATION_PROPOSAL must include rule_evaluation, evidence, why_not."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post(
        "/capability-evolution/decision",
        json={
            "case_id": "CASE-001",
            "po_id": "PO-1001",
            "exception_type": "budget_exceeded",
            "business_action": "request_purchase_order_approval",
        },
    )
    assert response.status_code == 200
    body = response.json()
    # CASE-001 has a trusted capability in demo data → USE_TRUSTED_CAPABILITY.
    assert body["decision"] == "USE_TRUSTED_CAPABILITY"
    # Explainability fields are present (backward-compatible additions).
    assert "rule_evaluation" in body
    assert "evidence" in body
    assert "pattern_snapshot" in body
    assert "why_not" in body


def test_capability_evolution_decision_xaml_workflow_has_why_not(monkeypatch):
    """XAML_WORKFLOW_PROPOSAL must include why_not API_MODERNIZATION_PROPOSAL."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post(
        "/capability-evolution/decision",
        json={
            "case_id": "CASE-003",
            "po_id": "PO-1003",
            "exception_type": "inventory_shortage",
            "business_action": "request_inventory_review",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "XAML_WORKFLOW_PROPOSAL"

    # Explainability fields.
    assert "rule_evaluation" in body
    assert "why_not" in body
    assert "API_MODERNIZATION_PROPOSAL" in body["why_not"]
    assert "evidence" in body or "evidence_run_ids" in body


def test_capability_evolution_decision_wait_for_vendor_has_why_not(monkeypatch):
    """WAIT_FOR_VENDOR_INFO must include api_modernization_required: false + why_not."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post(
        "/capability-evolution/decision",
        json={
            "case_id": "CASE-002",
            "po_id": "PO-1002",
            "exception_type": "vendor_info_missing",
            "business_action": "handle_vendor_info_missing",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "WAIT_FOR_VENDOR_INFO"
    assert body["api_modernization_required"] is False
    assert "why_not" in body
    assert "API_MODERNIZATION_PROPOSAL" in body["why_not"]


# ---------------------------------------------------------------------------
# PO-1000 / CASE-000 — normal case, precheck router, NO_EVOLUTION_REQUIRED
# ---------------------------------------------------------------------------

def po_1000_payload():
    return {
        "case_id": "CASE-000",
        "po_id": "PO-1000",
        "amount": 6000,
        "budget_limit": 10000,
        "vendor_id": "V-100",
        "vendor_info_complete": True,
        "inventory_available": True,
        "erp_status": "Normal",
        "raw_exception_text": "",
    }


def test_precheck_po_1000_returns_normal(monkeypatch):
    """/precheck PO-1000 -> NORMAL / STANDARD_PROCESSING / agent_required=false."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post("/precheck", json=po_1000_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["precheck_result"] == "NORMAL"
    assert body["case_type"] == "normal"
    assert body["route"] == "STANDARD_PROCESSING"
    assert body["agent_required"] is False
    assert body["exception_detected"] is False
    assert body["next_stage"] == "STANDARD_PROCESSING"
    assert body["recommended_next"] == "STANDARD_PROCESSING"


def test_precheck_po_1001_returns_clear_exception(monkeypatch):
    """/precheck PO-1001 (budget exceeded) -> CLEAR_EXCEPTION / CALL_TRIAGE_AGENT."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post("/precheck", json=po_1001_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["precheck_result"] == "CLEAR_EXCEPTION"
    assert body["case_type"] == "exception"
    assert body["route"] == "CALL_TRIAGE_AGENT"
    assert body["agent_required"] is True
    assert body["exception_detected"] is True
    assert body["exception_hint"] == "budget_exceeded"
    assert body["next_stage"] == "WAITING_FOR_TRIAGE"
    assert body["recommended_next"] == "CALL_TRIAGE_AGENT"


def test_precheck_ambiguous_unknown_erp_with_text(monkeypatch):
    """/precheck with unknown erp_status + exception text -> AMBIGUOUS."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    payload = po_1000_payload()
    payload["erp_status"] = "PendingReview"  # not a known normal or exception status
    payload["raw_exception_text"] = "something looks off"
    response = client.post("/precheck", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["precheck_result"] == "AMBIGUOUS"
    assert body["case_type"] == "ambiguous"
    assert body["route"] == "AGENT_SEMANTIC_ROUTING"
    assert body["agent_required"] is True
    assert body["next_stage"] == "AGENT_SEMANTIC_ROUTING"
    assert body["recommended_next"] == "AGENT_SEMANTIC_ROUTING"
    assert body["exception_detected"] == "uncertain"


def test_precheck_ambiguous_exception_without_field_signal(monkeypatch):
    """/precheck erp=Exception but all fields ok and no text -> AMBIGUOUS."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    payload = po_1000_payload()
    payload["erp_status"] = "Exception"  # erp says exception
    # but amount within budget, vendor complete, inventory available, no text
    payload["raw_exception_text"] = ""
    response = client.post("/precheck", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["precheck_result"] == "AMBIGUOUS"
    assert body["case_type"] == "ambiguous"
    assert body["route"] == "AGENT_SEMANTIC_ROUTING"


def test_triage_po_1000_returns_none_normal(monkeypatch):
    """/triage PO-1000 -> detected_exception_type=none, case normal, low risk."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post("/triage", json=po_1000_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["detected_exception_type"] == "none"
    assert body["risk_level"] == "low"
    assert body["confidence"] >= 0.95
    assert body["next_stage"] == "STANDARD_PROCESSING"
    assert body["requires_human_approval"] is False
    assert body["business_action"] == "standard_purchase_order_processing"


def test_capability_evolution_decision_case_000_no_evolution_required(monkeypatch):
    """/capability-evolution/decision CASE-000 -> NO_EVOLUTION_REQUIRED."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post(
        "/capability-evolution/decision",
        json={
            "case_id": "CASE-000",
            "po_id": "PO-1000",
            "exception_type": "none",
            "business_action": "standard_purchase_order_processing",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "NO_EVOLUTION_REQUIRED"
    assert body["business_action"] == "standard_purchase_order_processing"
    assert body["exception_type"] == "none"
    assert body["api_modernization_required"] is False
    assert body["xaml_improvement_required"] is False
    assert body["requires_human_approval"] is False
    assert "normal" in body["reason"].lower()
    # Explainability fields still present.
    assert "rule_evaluation" in body
    assert "why_not" in body
    assert "API_MODERNIZATION_PROPOSAL" in body["why_not"]
    assert "XAML_WORKFLOW_PROPOSAL" in body["why_not"]


def test_case_dashboard_case_000_static_returns_200(monkeypatch):
    """GET /case-dashboard/CASE-000 (no run) returns 200 with static fallback."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.get("/case-dashboard/CASE-000")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "CASE-000" in html
    assert "PO-1000" in html
    assert "STANDARD_PROCESSING" in html
    assert "NO_EVOLUTION_REQUIRED" in html


# ---------------------------------------------------------------------------
# Case Portfolio / Routing Overview
# ---------------------------------------------------------------------------

def test_case_portfolio_returns_200_with_all_four_cases(monkeypatch):
    """GET /case-portfolio returns 200 and includes all 4 cases."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.get("/case-portfolio")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # All 4 case IDs present.
    assert "CASE-000" in html
    assert "CASE-001" in html
    assert "CASE-002" in html
    assert "CASE-003" in html

    # All 4 PO IDs present.
    assert "PO-1000" in html
    assert "PO-1001" in html
    assert "PO-1002" in html
    assert "PO-1003" in html


def test_case_portfolio_contains_all_routes(monkeypatch):
    """Portfolio page contains the canonical route labels for all 4 cases."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    html = client.get("/case-portfolio").text

    assert "STANDARD_PROCESSING" in html
    assert "WAITING_FOR_HUMAN_APPROVAL" in html
    assert "WAITING_VENDOR_INFO" in html
    assert "CAPABILITY_GAP_DETECTED" in html


def test_case_portfolio_contains_all_evolution_decisions(monkeypatch):
    """Portfolio page contains the evolution decisions for all 4 cases."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    html = client.get("/case-portfolio").text

    assert "NO_EVOLUTION_REQUIRED" in html
    assert "WAIT_FOR_VENDOR_INFO" in html
    assert "XAML_WORKFLOW_PROPOSAL" in html
    # CASE-001 static fallback shows USE_TRUSTED_CAPABILITY.
    assert "USE_TRUSTED_CAPABILITY" in html


def test_case_portfolio_contains_portfolio_summary_and_governance_signals(monkeypatch):
    """Portfolio page contains the Summary and Governance Signals sections."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    html = client.get("/case-portfolio").text

    # Summary section.
    assert "Portfolio Summary" in html
    assert "Total Cases" in html
    assert "Normal" in html
    assert "Exception" in html
    assert "Waiting" in html
    assert "Capability Gap" in html

    # Governance signals section.
    assert "Governance Signals" in html
    assert "Human Approval Gate" in html
    assert "Validation Gate" in html
    assert "Proposal Lifecycle" in html
    assert "Trusted Registry" in html


def test_case_portfolio_contains_dashboard_links(monkeypatch):
    """Each case row links to /case-dashboard/{case_id} (with or without run_id)."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    html = client.get("/case-portfolio").text

    # Without real runs, links go to /case-dashboard/{case_id}.
    assert 'href="/case-dashboard/CASE-000"' in html
    assert 'href="/case-dashboard/CASE-001"' in html
    assert 'href="/case-dashboard/CASE-002"' in html
    assert 'href="/case-dashboard/CASE-003"' in html


def test_case_portfolio_falls_back_when_no_run_memory(monkeypatch, tmp_path):
    """Portfolio renders all 4 cases even with no real run memory at all."""
    # Point AUTOMATION_MEMORY_DIR at an empty tmp dir so no real runs exist.
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key",
                   memory_dir=tmp_path)
    client = TestClient(app)

    response = client.get("/case-portfolio")
    assert response.status_code == 200
    html = response.text
    # All 4 cases still present via static fallback.
    for case_id in ("CASE-000", "CASE-001", "CASE-002", "CASE-003"):
        assert case_id in html
    # "static fallback" badge should be present for cases without runs.
    assert "static fallback" in html


def test_case_portfolio_description_present(monkeypatch):
    """Portfolio page includes the routing description text."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    html = client.get("/case-portfolio").text

    assert "deterministic precheck" in html.lower()
    assert "structured memory" in html.lower()
    assert "not" in html.lower() and "auto-executed" in html.lower()


# ---------------------------------------------------------------------------
# PO-1004 / CASE-004 — ambiguous / low-confidence manual investigation proof
# ---------------------------------------------------------------------------

def po_1004_payload():
    return {
        "case_id": "CASE-004",
        "po_id": "PO-1004",
        "amount": 9500,
        "budget_limit": 10000,
        "vendor_id": "V-404",
        "vendor_info_complete": True,
        "inventory_available": True,
        "erp_status": "PendingReview",
        "raw_exception_text": "Needs business attention before processing.",
    }


def test_precheck_po_1004_returns_ambiguous(monkeypatch):
    """/precheck PO-1004 -> AMBIGUOUS with exception_detected=uncertain."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post("/precheck", json=po_1004_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["precheck_result"] == "AMBIGUOUS"
    assert body["case_type"] == "ambiguous"
    assert body["agent_required"] is True
    assert body["exception_detected"] == "uncertain"
    assert body["recommended_next"] == "AGENT_SEMANTIC_ROUTING"
    assert body["next_stage"] == "AGENT_SEMANTIC_ROUTING"
    assert "insufficient" in body["reason"].lower() or "ambiguous" in body["reason"].lower()


def test_triage_po_1004_returns_unknown_exception_low_confidence(monkeypatch):
    """/triage PO-1004 -> unknown_exception, confidence<0.75, WAITING_MANUAL_INVESTIGATION."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post("/triage", json=po_1004_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["detected_exception_type"] == "unknown_exception"
    assert body["risk_level"] == "unknown"
    assert body["confidence"] < 0.75
    assert body["recommended_path"] == "manual_investigation"
    assert body["next_stage"] == "WAITING_MANUAL_INVESTIGATION"
    assert body["requires_human_approval"] is True
    assert body["fallback"] == "manual_investigation"
    assert "low confidence" in body["reasoning_summary"].lower()


def test_capability_evolution_decision_case_004_manual_investigation(monkeypatch):
    """/capability-evolution/decision CASE-004 -> MANUAL_INVESTIGATION with why_not."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post(
        "/capability-evolution/decision",
        json={
            "case_id": "CASE-004",
            "po_id": "PO-1004",
            "exception_type": "unknown_exception",
            "business_action": "manual_case_review",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "MANUAL_INVESTIGATION"
    assert body["case_type"] == "ambiguous"
    assert body["business_action"] == "manual_case_review"
    assert body["exception_type"] == "unknown_exception"
    assert body["api_modernization_required"] is False
    assert body["xaml_improvement_required"] is False
    assert body["requires_human_review"] is True
    assert "human investigation" in body["reason"].lower()
    # why_not explains why not the alternatives.
    assert "why_not" in body
    assert "API_MODERNIZATION_PROPOSAL" in body["why_not"]
    assert "XAML_WORKFLOW_PROPOSAL" in body["why_not"]
    assert "NO_EVOLUTION_REQUIRED" in body["why_not"]


def test_case_dashboard_case_004_static_returns_200(monkeypatch):
    """GET /case-dashboard/CASE-004 (no run) returns 200 with static fallback."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.get("/case-dashboard/CASE-004")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "CASE-004" in html
    assert "PO-1004" in html
    assert "WAITING_MANUAL_INVESTIGATION" in html
    assert "MANUAL_INVESTIGATION" in html


# ---------------------------------------------------------------------------
# Case Intake Router + Router Lab
# ---------------------------------------------------------------------------

def test_case_intake_route_po_1000_normal(monkeypatch):
    """/case-intake/route PO-1000 -> NORMAL -> STANDARD_PROCESSING -> NO_EVOLUTION_REQUIRED."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post("/case-intake/route", json=po_1000_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "CASE-000"
    assert body["po_id"] == "PO-1000"
    assert body["case_type"] == "normal"
    assert body["precheck_result"] == "NORMAL"
    assert body["agent_required"] is False
    assert body["triage_result"] is None
    assert body["final_route"] == "STANDARD_PROCESSING"
    assert body["next_stage"] == "STANDARD_PROCESSING"
    assert body["capability_decision"] == "NO_EVOLUTION_REQUIRED"
    assert body["human_required"] is False
    assert body["execution_allowed"] is True
    assert body["auto_modernization_allowed"] is False
    assert body["recommended_uipath_workflow"] == "RouteProof_PO1000.xaml"
    assert body["dashboard_url"] == "http://localhost:8002/case-dashboard/CASE-000"
    assert body["portfolio_url"] == "http://localhost:8002/case-portfolio"


def test_case_intake_route_po_1001_budget_exceeded(monkeypatch):
    """/case-intake/route PO-1001 -> CLEAR_EXCEPTION -> budget_exceeded -> WAITING_FOR_HUMAN_APPROVAL."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post("/case-intake/route", json=po_1001_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "CASE-001"
    assert body["precheck_result"] == "CLEAR_EXCEPTION"
    assert body["agent_required"] is True
    assert body["detected_exception_type"] == "budget_exceeded"
    assert body["final_route"] == "WAITING_FOR_HUMAN_APPROVAL"
    assert body["human_required"] is True
    assert body["execution_allowed"] is False
    # Capability decision is one of these two (depends on seeded pattern data).
    assert body["capability_decision"] in {
        "USE_TRUSTED_CAPABILITY",
        "API_MODERNIZATION_PROPOSAL",
    }
    assert body["recommended_uipath_workflow"] == "Main.xaml"


def test_case_intake_route_po_1002_vendor_info_missing(monkeypatch):
    """/case-intake/route PO-1002 -> CLEAR_EXCEPTION -> vendor_info_missing -> WAITING_VENDOR_INFO."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    payload = {
        "case_id": "CASE-002", "po_id": "PO-1002", "amount": 6000,
        "budget_limit": 10000, "vendor_id": None,
        "vendor_info_complete": False, "inventory_available": True,
        "erp_status": "Normal", "raw_exception_text": "Vendor information missing",
    }
    response = client.post("/case-intake/route", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "CASE-002"
    assert body["precheck_result"] == "CLEAR_EXCEPTION"
    assert body["detected_exception_type"] == "vendor_info_missing"
    assert body["final_route"] == "WAITING_VENDOR_INFO"
    assert body["capability_decision"] == "WAIT_FOR_VENDOR_INFO"
    assert body["human_required"] is True
    assert body["execution_allowed"] is False
    assert body["recommended_uipath_workflow"] == "RouteProof_PO1002.xaml"


def test_case_intake_route_po_1003_inventory_shortage(monkeypatch):
    """/case-intake/route PO-1003 -> CLEAR_EXCEPTION -> inventory_shortage -> XAML_WORKFLOW_PROPOSAL."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    payload = {
        "case_id": "CASE-003", "po_id": "PO-1003", "amount": 8500,
        "budget_limit": 10000, "vendor_id": "V-118",
        "vendor_info_complete": True, "inventory_available": False,
        "erp_status": "Normal", "raw_exception_text": "Inventory shortage",
    }
    response = client.post("/case-intake/route", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "CASE-003"
    assert body["precheck_result"] == "CLEAR_EXCEPTION"
    assert body["detected_exception_type"] == "inventory_shortage"
    # triage next_stage for inventory_shortage is CAPABILITY_GAP_DETECTED.
    assert body["final_route"] == "CAPABILITY_GAP_DETECTED"
    assert body["capability_decision"] == "XAML_WORKFLOW_PROPOSAL"
    assert body["human_required"] is True
    assert body["execution_allowed"] is False
    assert body["recommended_uipath_workflow"] == "RouteProof_PO1003.xaml"


def test_case_intake_route_po_1004_ambiguous_manual_investigation(monkeypatch):
    """/case-intake/route PO-1004 -> AMBIGUOUS -> unknown_exception -> MANUAL_INVESTIGATION."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post("/case-intake/route", json=po_1004_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == "CASE-004"
    assert body["precheck_result"] == "AMBIGUOUS"
    assert body["case_type"] == "ambiguous"
    assert body["agent_required"] is True
    assert body["detected_exception_type"] == "unknown_exception"
    assert body["confidence"] < 0.75
    assert body["final_route"] == "WAITING_MANUAL_INVESTIGATION"
    assert body["next_stage"] == "WAITING_MANUAL_INVESTIGATION"
    assert body["capability_decision"] == "MANUAL_INVESTIGATION"
    assert body["human_required"] is True
    assert body["execution_allowed"] is False
    assert body["recommended_uipath_workflow"] == "RouteProof_PO1004.xaml"


def test_case_router_lab_returns_200_with_5_cases_and_workflows(monkeypatch):
    """GET /case-router-lab returns 200 with all 5 cases and 5 workflow names."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.get("/case-router-lab")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # All 5 case IDs.
    for case_id in ("CASE-000", "CASE-001", "CASE-002", "CASE-003", "CASE-004"):
        assert case_id in html

    # All 5 recommended workflow names.
    for workflow in (
        "RouteProof_PO1000.xaml",
        "Main.xaml",
        "RouteProof_PO1002.xaml",
        "RouteProof_PO1003.xaml",
        "RouteProof_PO1004.xaml",
    ):
        assert workflow in html

    # Key routing labels present.
    assert "STANDARD_PROCESSING" in html
    assert "WAITING_FOR_HUMAN_APPROVAL" in html
    assert "WAITING_VENDOR_INFO" in html
    assert "XAML_WORKFLOW_PROPOSAL" in html
    assert "MANUAL_INVESTIGATION" in html
    assert "NO_EVOLUTION_REQUIRED" in html

    # Description text present.
    assert "deterministic" in html.lower() or "rules handle" in html.lower()
    assert "auto-executes" in html.lower() or "never auto" in html.lower()


def test_case_portfolio_includes_case_004(monkeypatch):
    """Portfolio now includes 5 cases: CASE-000/001/002/003/004."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    html = client.get("/case-portfolio").text

    assert "CASE-004" in html
    assert "PO-1004" in html
    assert "WAITING_MANUAL_INVESTIGATION" in html
    assert "MANUAL_INVESTIGATION" in html
    # All 5 cases present.
    for case_id in ("CASE-000", "CASE-001", "CASE-002", "CASE-003", "CASE-004"):
        assert case_id in html


# ---------------------------------------------------------------------------
# Governance Policy Gate
# ---------------------------------------------------------------------------

def _policy_gate_payload(case_id, po_id, case_type, precheck, exception,
                         confidence, route, decision, exec_mode="NONE"):
    """Helper to build a /policy-gate/evaluate request body."""
    return {
        "case_id": case_id,
        "po_id": po_id,
        "case_type": case_type,
        "precheck_result": precheck,
        "detected_exception_type": exception,
        "confidence": confidence,
        "final_route": route,
        "capability_decision": decision,
        "execution_mode": exec_mode,
    }


def test_policy_gate_case_000_allow_standard_processing(monkeypatch):
    """/policy-gate/evaluate CASE-000 -> ALLOW_STANDARD_PROCESSING."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    response = client.post("/policy-gate/evaluate", json=_policy_gate_payload(
        "CASE-000", "PO-1000", "normal", "NORMAL", "none",
        None, "STANDARD_PROCESSING", "NO_EVOLUTION_REQUIRED", "STANDARD",
    ))
    assert response.status_code == 200
    body = response.json()
    assert body["policy_decision"] == "ALLOW_STANDARD_PROCESSING"
    assert body["execution_allowed"] is True
    assert body["human_required"] is False
    assert body["required_gates"] == []
    assert body["blocked_actions"] == []


def test_policy_gate_case_001_require_human_approval(monkeypatch):
    """/policy-gate/evaluate CASE-001 -> REQUIRE_HUMAN_APPROVAL."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    response = client.post("/policy-gate/evaluate", json=_policy_gate_payload(
        "CASE-001", "PO-1001", "exception", "CLEAR_EXCEPTION", "budget_exceeded",
        0.94, "WAITING_FOR_HUMAN_APPROVAL", "USE_TRUSTED_CAPABILITY", "API",
    ))
    assert response.status_code == 200
    body = response.json()
    assert body["policy_decision"] == "REQUIRE_HUMAN_APPROVAL"
    assert body["execution_allowed"] is False
    assert body["human_required"] is True
    assert body["validation_required"] is True
    assert "BUSINESS_APPROVAL" in body["required_gates"]
    assert "VALIDATION_GATE" in body["required_gates"]
    assert "AUTO_EXECUTE_WITHOUT_APPROVAL" in body["blocked_actions"]
    assert body["audit_required"] is True


def test_policy_gate_case_002_wait_for_business_data(monkeypatch):
    """/policy-gate/evaluate CASE-002 -> WAIT_FOR_BUSINESS_DATA."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    response = client.post("/policy-gate/evaluate", json=_policy_gate_payload(
        "CASE-002", "PO-1002", "exception", "CLEAR_EXCEPTION", "vendor_info_missing",
        0.82, "WAITING_VENDOR_INFO", "WAIT_FOR_VENDOR_INFO", "RPA",
    ))
    assert response.status_code == 200
    body = response.json()
    assert body["policy_decision"] == "WAIT_FOR_BUSINESS_DATA"
    assert body["execution_allowed"] is False
    assert body["human_required"] is True
    assert "BUSINESS_DATA_COMPLETION" in body["required_gates"]


def test_policy_gate_case_003_require_capability_review(monkeypatch):
    """/policy-gate/evaluate CASE-003 -> REQUIRE_CAPABILITY_REVIEW (proposal_only)."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    response = client.post("/policy-gate/evaluate", json=_policy_gate_payload(
        "CASE-003", "PO-1003", "capability_gap", "CLEAR_EXCEPTION", "inventory_shortage",
        0.88, "CAPABILITY_GAP_DETECTED", "XAML_WORKFLOW_PROPOSAL", "RPA",
    ))
    assert response.status_code == 200
    body = response.json()
    assert body["policy_decision"] == "REQUIRE_CAPABILITY_REVIEW"
    assert body["execution_allowed"] is False
    assert body["human_required"] is True
    assert body.get("proposal_only") is True
    assert "CAPABILITY_REVIEW" in body["required_gates"]
    assert "PROPOSAL_APPROVAL" in body["required_gates"]
    assert "AUTO_GENERATE_XAML" in body["blocked_actions"]
    assert "AUTO_DEPLOY_WORKFLOW" in body["blocked_actions"]


def test_policy_gate_case_004_require_manual_investigation(monkeypatch):
    """/policy-gate/evaluate CASE-004 -> REQUIRE_MANUAL_INVESTIGATION (low confidence)."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    response = client.post("/policy-gate/evaluate", json=_policy_gate_payload(
        "CASE-004", "PO-1004", "ambiguous", "AMBIGUOUS", "unknown_exception",
        0.4, "WAITING_MANUAL_INVESTIGATION", "MANUAL_INVESTIGATION", "NONE",
    ))
    assert response.status_code == 200
    body = response.json()
    assert body["policy_decision"] == "REQUIRE_MANUAL_INVESTIGATION"
    assert body["execution_allowed"] is False
    assert body["human_required"] is True
    assert "MANUAL_INVESTIGATION" in body["required_gates"]
    assert "CAPABILITY_REUSE" in body["blocked_actions"]


def test_policy_gate_api_modernization_proposal_blocked(monkeypatch):
    """Any PROPOSAL-type capability decision must have execution_allowed=false.

    For budget_exceeded + API_MODERNIZATION_PROPOSAL, the stricter
    REQUIRE_HUMAN_APPROVAL rule takes priority (budget exceptions always need
    business approval), but execution is still blocked.
    """
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    response = client.post("/policy-gate/evaluate", json=_policy_gate_payload(
        "CASE-X", "PO-X", "exception", "CLEAR_EXCEPTION", "budget_exceeded",
        0.95, "WAITING_FOR_HUMAN_APPROVAL", "API_MODERNIZATION_PROPOSAL", "API",
    ))
    assert response.status_code == 200
    body = response.json()
    # Budget exceeded always requires human approval (stricter rule wins).
    assert body["policy_decision"] == "REQUIRE_HUMAN_APPROVAL"
    assert body["execution_allowed"] is False
    assert body["human_required"] is True
    assert "AUTO_EXECUTE_WITHOUT_APPROVAL" in body["blocked_actions"]

    # A pure proposal (not budget_exceeded, not vendor, not low confidence)
    # must fall into REQUIRE_CAPABILITY_REVIEW with proposal_only=true.
    response2 = client.post("/policy-gate/evaluate", json=_policy_gate_payload(
        "CASE-Y", "PO-Y", "exception", "CLEAR_EXCEPTION", "selector_fragility",
        0.90, "CAPABILITY_REVIEW_PENDING", "XAML_IMPROVEMENT_PROPOSAL", "RPA",
    ))
    body2 = response2.json()
    assert body2["policy_decision"] == "REQUIRE_CAPABILITY_REVIEW"
    assert body2["execution_allowed"] is False
    assert body2.get("proposal_only") is True


def test_case_intake_route_includes_policy_gate(monkeypatch):
    """/case-intake/route response includes the policy_gate field."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    response = client.post("/case-intake/route", json=po_1000_payload())
    assert response.status_code == 200
    body = response.json()
    # Backward-compatible: old fields still present.
    assert body["final_route"] == "STANDARD_PROCESSING"
    assert body["capability_decision"] == "NO_EVOLUTION_REQUIRED"
    # New policy_gate field present.
    assert "policy_gate" in body
    gate = body["policy_gate"]
    assert gate["policy_decision"] == "ALLOW_STANDARD_PROCESSING"
    assert gate["execution_allowed"] is True
    assert gate["human_required"] is False


def test_case_intake_route_policy_gate_case_001(monkeypatch):
    """/case-intake/route PO-001 policy_gate -> REQUIRE_HUMAN_APPROVAL."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    response = client.post("/case-intake/route", json=po_1001_payload())
    body = response.json()
    gate = body["policy_gate"]
    assert gate["policy_decision"] == "REQUIRE_HUMAN_APPROVAL"
    assert gate["execution_allowed"] is False
    assert gate["human_required"] is True


def test_company_context_endpoint_returns_mock_enterprise_context(monkeypatch):
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.get("/company-context")

    assert response.status_code == 200
    body = response.json()
    assert body["company"]["name"] == "Demo Manufacturing Group"
    assert body["enterprise_context_source"] == "mock_enterprise_context"
    assert body["enterprise_context_mode"] == "local_demo_snapshot"
    assert body["company"]["finance_policy"]["strict_vendor_compliance"] is True
    assert "protect strategic customer renewals" in body["company"]["strategic_goals"]


def test_case_intake_route_accepts_business_remarks_and_uses_company_context(monkeypatch):
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    payload = po_1001_payload()
    payload.update({
        "business_remarks": (
            "Q4 customer delivery is at risk. Finance asks whether this should "
            "be approved due to strategic account impact."
        ),
        "agent_context_policy": "fetch_enterprise_context_before_decision",
    })

    response = client.post("/case-intake/route", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["business_remarks"].startswith("Q4 customer delivery")
    assert body["enterprise_context_source"] == "mock_enterprise_context"
    assert body["route_agent_mode"] == "mock_llm_with_mock_enterprise_context"
    assert body["agent_required"] is True
    assert body["agent_context_used"] is True
    assert body["company_context_reference"]["finance_policy_used"] is True
    assert body["company_context_reference"]["sales_context_used"] is True
    assert body["company_context_reference"]["operations_context_used"] is True
    assert body["agent_reasoning_summary"]
    proof = body["llm_validation_proof"]
    assert proof["reasoning_mode"] == "llm_backed"
    assert proof["llm_enabled"] is True
    assert proof["llm_call_mode"] == "mock"
    assert proof["llm_provider"] == "deepseek"
    assert proof["schema_validated"] is True
    assert proof["guardrails_applied"] is True
    assert proof["decision_status"] == "DECISION_READY"
    assert proof["llm_invocation_verified"] is True
    action = body["recommended_erp_action"]
    assert action["action_id"] == "CREATE_WEB_APPROVAL_TASK"
    assert action["button_selector_id"] is None


def test_case_intake_route_accepts_business_action_hint(monkeypatch):
    """Route response echoes optional business_action for Pattern Memory grouping."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    payload = po_1001_payload()
    payload.update({
        "business_action": "request_capex_budget_exception_approval",
        "business_remarks": "Q4 capital equipment delivery is at risk.",
        "agent_context_policy": "fetch_enterprise_context_before_decision",
    })

    response = client.post("/case-intake/route", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["business_action"] == "request_capex_budget_exception_approval"
    assert body["detected_exception_type"] == "budget_exceeded"
    assert body["agent_context_used"] is True


def test_case_intake_route_normal_is_explicit_deterministic_precheck(monkeypatch):
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.post("/case-intake/route", json=po_1000_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["agent_required"] is False
    assert body["precheck_decision_source"] == "deterministic_rule"
    assert body["enterprise_context_source"] == "not_used"
    assert body["route_agent_mode"] == "deterministic_precheck_no_enterprise_context"
    assert body["agent_context_used"] is False
    assert body["llm_validation_proof"]["reasoning_mode"] == "deterministic_rule"
    assert body["llm_validation_proof"]["llm_invocation_verified"] is False
    assert body["recommended_erp_action"]["button_selector_id"] == (
        "ctl00_MainContent_btnMarkStandardProcessed"
    )


def test_case_intake_route_real_llm_mode_sets_invocation_verified(monkeypatch):
    app = load_app(monkeypatch, demo_mode=None, api_key="test-key")
    import app.main as main

    def fake_call_llm_json(_prompt, config, _agent_name, request_id):
        return (
            json.dumps({
                "final_route": "WAITING_FOR_HUMAN_APPROVAL",
                "policy_gate": "REQUIRE_HUMAN_APPROVAL",
                "explanation": "Real-mode LLM proof used enterprise context.",
                "confidence": 0.91,
                "context_signals_used": ["company_context", "business_remarks"],
            }),
            main.LlmCallEvidence(
                llm_call_mode="real",
                llm_provider=config.provider,
                llm_model=config.model,
                llm_request_id=request_id,
                llm_latency_ms=1,
                llm_invocation_verified=True,
            ),
        )

    monkeypatch.setattr(main, "call_llm_json", fake_call_llm_json)
    client = TestClient(app)
    payload = po_1001_payload()
    payload["business_remarks"] = "Q4 customer delivery is at risk."

    response = client.post("/case-intake/route", json=payload)

    assert response.status_code == 200
    body = response.json()
    proof = body["llm_validation_proof"]
    assert proof["llm_enabled"] is True
    assert proof["llm_call_mode"] == "real"
    assert proof["llm_provider"] == "deepseek"
    assert proof["llm_invocation_verified"] is True
    assert body["enterprise_context_source"] == "mock_enterprise_context"
    assert body["route_agent_mode"] == "real_llm_with_mock_enterprise_context"


def test_policy_gate_lab_returns_200_with_5_cases(monkeypatch):
    """GET /policy-gate/lab returns 200 with all 5 cases and policy decisions."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)
    response = client.get("/policy-gate/lab")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # All 5 case IDs.
    for case_id in ("CASE-000", "CASE-001", "CASE-002", "CASE-003", "CASE-004"):
        assert case_id in html

    # All 5 policy decisions.
    for policy in (
        "ALLOW_STANDARD_PROCESSING",
        "REQUIRE_HUMAN_APPROVAL",
        "WAIT_FOR_BUSINESS_DATA",
        "REQUIRE_CAPABILITY_REVIEW",
        "REQUIRE_MANUAL_INVESTIGATION",
    ):
        assert policy in html

    # Rules section present.
    assert "Policy Rules" in html
    assert "auto_execution_allowed" in html.lower() or "auto-execution" in html.lower()


# ---------------------------------------------------------------------------
# Demo Evidence Export (snapshot + markdown)
# ---------------------------------------------------------------------------

def test_demo_evidence_snapshot_returns_full_json(monkeypatch):
    """GET /demo/evidence-snapshot returns JSON with all required sections."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.get("/demo/evidence-snapshot")
    assert response.status_code == 200
    body = response.json()

    # Top-level structure.
    assert body["project"] == "Agentic ERP Modernization Layer"
    for key in ("cases", "routes", "policy_gates", "capability_decisions",
                "dashboards", "governance", "safety_boundaries",
                "recommended_demo_order"):
        assert key in body, f"missing key: {key}"

    # 5 cases.
    assert len(body["cases"]) == 5
    case_ids = [c["case_id"] for c in body["cases"]]
    for expected in ("CASE-000", "CASE-001", "CASE-002", "CASE-003", "CASE-004"):
        assert expected in case_ids

    # Each case has the required fields.
    for c in body["cases"]:
        for field in ("case_id", "po_id", "case_type", "precheck_result",
                      "detected_exception_type", "confidence", "latest_run_id",
                      "run_memory_status", "recommended_workflow",
                      "dashboard_url", "policy_decision"):
            assert field in c, f"case missing field: {field}"

    # Safety boundaries present and all true.
    sb = body["safety_boundaries"]
    for key in ("no_auto_xaml_modification", "no_auto_api_deployment",
                "no_automatic_trusted_registration", "proposal_requires_review",
                "windows_xaml_unchanged"):
        assert sb[key] is True, f"safety boundary {key} must be True"

    # Governance present.
    gov = body["governance"]
    for key in ("human_approval_gate", "validation_gate", "proposal_lifecycle",
                "trusted_capability_registry", "auto_execution_allowed"):
        assert key in gov

    # Policy gates have 5 entries.
    assert len(body["policy_gates"]) == 5
    policy_decisions = [g["policy_decision"] for g in body["policy_gates"]]
    for expected in ("ALLOW_STANDARD_PROCESSING", "REQUIRE_HUMAN_APPROVAL",
                     "WAIT_FOR_BUSINESS_DATA", "REQUIRE_CAPABILITY_REVIEW",
                     "REQUIRE_MANUAL_INVESTIGATION"):
        assert expected in policy_decisions

    # Recommended demo order non-empty.
    assert len(body["recommended_demo_order"]) >= 5


def test_demo_evidence_snapshot_missing_run_memory_does_not_500(monkeypatch):
    """Evidence snapshot must not 500 when run memory is missing."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.get("/demo/evidence-snapshot")
    assert response.status_code == 200
    body = response.json()
    # At least some cases should be "missing" (no run memory in test env).
    statuses = [c["run_memory_status"] for c in body["cases"]]
    assert "missing" in statuses or "present" in statuses  # no 500 either way


def test_demo_evidence_markdown_returns_markdown(monkeypatch):
    """GET /demo/evidence-markdown returns text/markdown with 5 cases."""
    app = load_app(monkeypatch, demo_mode="mock_success", api_key="test-key")
    client = TestClient(app)

    response = client.get("/demo/evidence-markdown")
    assert response.status_code == 200
    assert "text/markdown" in response.headers["content-type"]
    md = response.text

    # Title.
    assert "# Agentic ERP Modernization Layer" in md
    # All 5 cases.
    for case_id in ("CASE-000", "CASE-001", "CASE-002", "CASE-003", "CASE-004"):
        assert case_id in md
    # Safety boundaries section.
    assert "## Safety Boundaries" in md
    assert "No auto-XAML modification" in md
    assert "No auto API deployment" in md
    # Tables present.
    assert "## Cases" in md
    assert "## Route Plans" in md
    assert "## Policy Gates" in md
    assert "## Governance Checklist" in md
    assert "## Dashboard Links" in md
    assert "## Recommended Demo Order" in md
    # Policy decisions present.
    for policy in ("ALLOW_STANDARD_PROCESSING", "REQUIRE_HUMAN_APPROVAL",
                   "WAIT_FOR_BUSINESS_DATA", "REQUIRE_CAPABILITY_REVIEW",
                   "REQUIRE_MANUAL_INVESTIGATION"):
        assert policy in md
