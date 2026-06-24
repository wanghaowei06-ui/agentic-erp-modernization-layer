from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parent
for path in (SERVICE_ROOT, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from business_rule_tests import validate_budget_exceeded_requires_approval
from contract_tests import validate_contract
from parity_check import APPROVAL_SIDE_EFFECTS, run_parity_check
from memory.repository import record_case_001_hard_mvp_artifacts
from memory.repository import record_inventory_shortage_gap
from shared.automation_memory.repository import record_capability_gap
from shared.automation_memory.repository import record_validation_result
from shared.automation_memory.repository import register_capability
from shared.automation_memory.repository import query_capabilities
from shared.automation_memory.repository import query_case_decisions
from shared.automation_memory.repository import query_case_timeline
from shared.automation_memory.repository import query_gaps

app = FastAPI(title="API Validation Gate Support Service", version="0.1.0")
memory_logger = logging.getLogger("validation-suite.memory")
memory_logger.setLevel(logging.INFO)
if not memory_logger.handlers:
    memory_handler = logging.StreamHandler()
    memory_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    memory_logger.addHandler(memory_handler)


class ValidationRequest(BaseModel):
    case_id: str = "CASE-001"
    correlation_id: str | None = None
    simulate_failure: bool = False


class ValidationResponse(BaseModel):
    case_id: str
    business_action: str
    contract_test: str
    business_rule_test: str
    rpa_api_parity_check: str
    same_initial_state: bool
    rpa_result: dict[str, object]
    api_result: dict[str, object]
    trusted_tool_candidate: bool
    requires_registration_approval: bool
    data_isolation: str | None = None
    rpa_test_case_id: str | None = None
    api_test_case_id: str | None = None
    compared_fields: list[str] | None = None
    parity_failure_reason: str | None = None
    matched_side_effects: list[str] | None = None
    missing_side_effects: list[str] | None = None
    extra_side_effects: list[str] | None = None
    parity_summary: str | None = None
    recommended_recovery: str | None = None


def validation_status_for(response: ValidationResponse) -> str:
    checks = [
        response.contract_test,
        response.business_rule_test,
        response.rpa_api_parity_check,
    ]
    return "passed" if all(check == "passed" for check in checks) else "failed"


def validation_memory_payload(response: ValidationResponse) -> dict[str, Any]:
    validation_status = validation_status_for(response)
    payload: dict[str, Any] = {
        "case_id": response.case_id,
        "po_id": response.api_test_case_id or response.rpa_test_case_id,
        "business_action": response.business_action,
        "validation_status": validation_status,
        "contract_test": response.contract_test,
        "business_rule_test": response.business_rule_test,
        "rpa_api_parity_check": response.rpa_api_parity_check,
        "same_initial_state": response.same_initial_state,
        "rpa_result": response.rpa_result,
        "api_result": response.api_result,
        "trusted_tool_candidate": response.trusted_tool_candidate,
        "requires_registration_approval": response.requires_registration_approval,
        "source_endpoint": "/validate/request-purchase-order-approval",
    }
    if response.parity_failure_reason:
        payload["failure_reason"] = response.parity_failure_reason
    if response.recommended_recovery:
        payload["recommended_recovery"] = response.recommended_recovery
    return payload


def trusted_capabilities() -> list[dict[str, Any]]:
    return [
        {
            "capability_id": "cap_api_request_po_approval_v1",
            "business_action": "request_purchase_order_approval",
            "capability_type": "api",
            "execution_mode": "API",
            "endpoint": (
                "http://localhost:8003/api/purchase-orders/{po_id}/approval-request"
            ),
            "status": "trusted",
            "validation_status": "passed",
            "schema_version": "1.0",
        },
        {
            "capability_id": "cap_human_po_approval_v1",
            "business_action": "request_purchase_order_approval",
            "capability_type": "human_task",
            "execution_mode": "HUMAN_APPROVAL",
            "workflow_name": "HumanApproval_PO1001",
            "status": "trusted",
            "validation_status": "passed",
            "schema_version": "1.0",
        },
    ]


def record_validation_memory(
    response: ValidationResponse,
    *,
    correlation_id: str | None = None,
) -> None:
    try:
        memory_payload = validation_memory_payload(response)
        record_validation_result(
            response.case_id or "CASE-UNKNOWN",
            memory_payload,
            source_service="validation-suite",
            correlation_id=correlation_id,
        )
        if memory_payload["validation_status"] == "passed":
            for capability in trusted_capabilities():
                register_capability(
                    capability,
                    source_service="validation-suite",
                    case_id=response.case_id,
                    correlation_id=correlation_id,
                )
    except Exception as exc:  # pragma: no cover - covered via monkeypatch test
        memory_logger.warning("Automation Memory validation write failed: %s", exc)


def record_gap_memory(gap: dict[str, object]) -> None:
    try:
        case_id = str(gap.get("case_id") or "CASE-UNKNOWN")
        record_capability_gap(
            case_id,
            {
                "case_id": case_id,
                "po_id": gap.get("po_id", "PO-1003"),
                "business_action": "resolve_inventory_shortage",
                "gap_type": "missing_trusted_capability",
                "gap_status": "open",
                "recommended_capability": gap.get(
                    "recommended_capability",
                    "HandleInventoryShortageReview.xaml",
                ),
                "priority": "medium",
                "source_endpoint": "/capability-gaps/inventory-shortage",
                "legacy_gap_payload": gap,
            },
            source_service="validation-suite",
        )
    except Exception as exc:  # pragma: no cover - covered via monkeypatch test
        memory_logger.warning("Automation Memory capability gap write failed: %s", exc)


def event_dict(event: Any) -> dict[str, Any]:
    return event.model_dump()


def memory_query_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={
            "error": "automation_memory_query_failed",
            "message": str(exc),
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "validation-suite"}


@app.get("/memory/cases/{case_id}")
def memory_case_summary(case_id: str) -> dict[str, object]:
    try:
        timeline = query_case_timeline(case_id)
    except Exception as exc:
        raise memory_query_error(exc) from exc
    latest = timeline[-1] if timeline else None
    return {
        "case_id": case_id,
        "event_count": len(timeline),
        "latest_event_type": str(latest.event_type) if latest else None,
        "latest_event_at": latest.created_at if latest else None,
        "empty": not timeline,
        "timeline": [event_dict(event) for event in timeline],
    }


@app.get("/memory/cases/{case_id}/timeline")
def memory_case_timeline(case_id: str) -> dict[str, object]:
    try:
        timeline = query_case_timeline(case_id)
    except Exception as exc:
        raise memory_query_error(exc) from exc
    return {
        "case_id": case_id,
        "empty": not timeline,
        "events": [event_dict(event) for event in timeline],
    }


@app.get("/memory/decisions/{case_id}")
def memory_case_decisions(case_id: str) -> dict[str, object]:
    try:
        decisions = query_case_decisions(case_id)
    except Exception as exc:
        raise memory_query_error(exc) from exc
    return {
        "case_id": case_id,
        "empty": not decisions,
        "events": [event_dict(event) for event in decisions],
    }


@app.get("/memory/capabilities")
def memory_capabilities() -> dict[str, object]:
    try:
        capabilities = query_capabilities()
    except Exception as exc:
        raise memory_query_error(exc) from exc
    return {
        "capabilities": [capability.model_dump() for capability in capabilities],
    }


@app.get("/memory/gaps")
def memory_gaps() -> dict[str, object]:
    try:
        gaps = query_gaps()
    except Exception as exc:
        raise memory_query_error(exc) from exc
    return {
        "gaps": [event_dict(event) for event in gaps],
    }


@app.post(
    "/validate/request-purchase-order-approval",
    response_model=ValidationResponse,
    response_model_exclude_none=True,
)
def validate_request_purchase_order_approval(
    payload: ValidationRequest | None = None,
) -> ValidationResponse:
    request_payload = {
        "approval_reason": "Amount exceeds budget limit",
        "manager_id": "MGR-001",
        "source_case_id": "CASE-001",
    }
    parity = run_parity_check(simulate_failure=bool(payload and payload.simulate_failure))
    response_payload = {
        "po_id": "PO-1001-API",
        "status": parity["api_result"]["status"],
        "audit_log_created": True,
        "execution_mode": "API",
        "source_case_id": "CASE-001",
    }
    contract_test = validate_contract(request_payload, response_payload)
    business_rule_test = validate_budget_exceeded_requires_approval(
        amount=18000,
        budget_limit=10000,
        resulting_status=str(parity["api_result"]["status"]),
    )

    if payload and payload.simulate_failure:
        response = ValidationResponse(
            case_id=payload.case_id,
            business_action="request_purchase_order_approval",
            contract_test=contract_test,
            business_rule_test=business_rule_test,
            rpa_api_parity_check=str(parity["rpa_api_parity_check"]),
            same_initial_state=bool(parity["same_initial_state"]),
            rpa_result=parity["rpa_result"],
            api_result=parity["api_result"],
            data_isolation=str(parity["data_isolation"]),
            rpa_test_case_id=str(parity["rpa_test_case_id"]),
            api_test_case_id=str(parity["api_test_case_id"]),
            parity_failure_reason=(
                "Simulated mismatch in audit log creation for demo failure path."
            ),
            matched_side_effects=parity["matched_side_effects"],
            missing_side_effects=parity["missing_side_effects"],
            extra_side_effects=parity["extra_side_effects"],
            parity_summary=str(parity["parity_summary"]),
            trusted_tool_candidate=False,
            requires_registration_approval=False,
            recommended_recovery=(
                "Keep execution mode as RPA, generate fix task, require IT review, "
                "and rerun validation."
            ),
        )
        record_validation_memory(response, correlation_id=payload.correlation_id)
        return response

    response = ValidationResponse(
        case_id=(payload.case_id if payload else "CASE-001"),
        business_action="request_purchase_order_approval",
        contract_test=contract_test,
        business_rule_test=business_rule_test,
        rpa_api_parity_check=str(parity["rpa_api_parity_check"]),
        same_initial_state=bool(parity["same_initial_state"]),
        rpa_result=parity["rpa_result"],
        api_result=parity["api_result"],
        data_isolation=str(parity["data_isolation"]),
        rpa_test_case_id=str(parity["rpa_test_case_id"]),
        api_test_case_id=str(parity["api_test_case_id"]),
        compared_fields=parity["compared_fields"],
        matched_side_effects=APPROVAL_SIDE_EFFECTS,
        missing_side_effects=parity["missing_side_effects"],
        extra_side_effects=parity["extra_side_effects"],
        parity_summary=str(parity["parity_summary"]),
        trusted_tool_candidate=True,
        requires_registration_approval=True,
    )
    record_case_001_hard_mvp_artifacts(response.model_dump(exclude_none=True))
    record_validation_memory(
        response,
        correlation_id=payload.correlation_id if payload else None,
    )
    return response


@app.post("/capability-gaps/inventory-shortage")
def capability_gap_inventory_shortage() -> dict[str, object]:
    gap = record_inventory_shortage_gap()
    record_gap_memory(gap)
    return gap
