from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.automation_memory.repository import record_execution_trace

from .db import (
    APPROVAL_SIDE_EFFECTS,
    fetch_audit_logs,
    fetch_audit_logs_for_po,
    fetch_purchase_order,
    fetch_purchase_orders,
    fetch_side_effect_trace,
    init_db,
    request_approval,
)

app = FastAPI(title="Mock Legacy ERP UI", version="0.1.0")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
memory_logger = logging.getLogger("mock-legacy-erp.memory")
memory_logger.setLevel(logging.INFO)
if not memory_logger.handlers:
    memory_handler = logging.StreamHandler()
    memory_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    memory_logger.addHandler(memory_handler)


CASE_DASHBOARD = [
    {
        "case_id": "CASE-001",
        "po_id": "PO-1001",
        "current_stage": "API_MODE_EXECUTED",
        "detected_exception_type": "budget_exceeded",
        "risk_level": "high",
        "assigned_actor": "UiPath case workflow",
        "waiting_for": "demo evidence capture",
        "execution_mode": "API",
        "validation_status": "passed",
        "trusted_tool_status": "registered",
        "path": "main path",
    },
    {
        "case_id": "CASE-002",
        "po_id": "PO-1002",
        "current_stage": "WAITING_VENDOR_INFO",
        "detected_exception_type": "vendor_info_missing",
        "risk_level": "medium",
        "assigned_actor": "vendor operations",
        "waiting_for": "vendor data update",
        "execution_mode": "RPA",
        "validation_status": "not_started",
        "trusted_tool_status": "not_generated",
        "path": "lightweight route",
    },
    {
        "case_id": "CASE-003",
        "po_id": "PO-1003",
        "current_stage": "WAITING_INVENTORY_REVIEW",
        "detected_exception_type": "inventory_shortage",
        "risk_level": "medium",
        "assigned_actor": "inventory review",
        "waiting_for": "stock confirmation",
        "execution_mode": "RPA",
        "validation_status": "roadmap",
        "trusted_tool_status": "roadmap",
        "path": "roadmap route",
    },
]

CASE_001_TIMELINE = [
    {"id": "timeline-case-selected", "label": "Case Selected"},
    {"id": "timeline-case-created", "label": "Case Created"},
    {"id": "timeline-rpa-extracted", "label": "RPA Extracted"},
    {"id": "timeline-agent-classified", "label": "Agent Classified: budget_exceeded"},
    {"id": "timeline-case-routed", "label": "Case Routed: WAITING_FOR_HUMAN_APPROVAL"},
    {"id": "timeline-human-approved", "label": "Human Approved"},
    {"id": "timeline-rpa-writeback", "label": "RPA Write-back Completed"},
    {"id": "timeline-validation-passed", "label": "Validation Passed"},
    {
        "id": "timeline-readiness-evaluated",
        "label": "Modernization Readiness Evaluated",
    },
    {"id": "timeline-plan-approved", "label": "Modernization Plan Approved"},
    {"id": "timeline-implementation-handoff", "label": "Implementation Handoff Ready"},
    {"id": "timeline-tool-registered", "label": "Trusted Tool Registered"},
    {"id": "timeline-api-mode-executed", "label": "API Mode Executed"},
]

DEMO_CASES = [
    {
        "case_id": "CASE-001",
        "po_id": "PO-1001",
        "exception_hint": "budget_exceeded",
        "business_action": "request_purchase_order_approval",
        "amount": 18000,
        "budget_limit": 10000,
        "estimated_impact": "high",
        "frequency_30d": 42,
        "api_candidate_hint": True,
        "recommended_entry": True,
    },
    {
        "case_id": "CASE-002",
        "po_id": "PO-1002",
        "exception_hint": "vendor_info_missing",
        "business_action": "request_vendor_information_update",
        "amount": 6000,
        "budget_limit": 10000,
        "estimated_impact": "medium",
        "frequency_30d": 8,
        "api_candidate_hint": False,
        "recommended_entry": False,
    },
    {
        "case_id": "CASE-003",
        "po_id": "PO-1003",
        "exception_hint": "inventory_shortage",
        "business_action": "request_inventory_review",
        "estimated_impact": "medium",
        "frequency_30d": 5,
        "api_candidate_hint": False,
        "recommended_entry": False,
    },
]

READINESS_FACTORS = {
    "business_action": "request_purchase_order_approval",
    "frequency": 0.82,
    "business_value": 0.90,
    "field_stability": 0.88,
    "ui_fragility": 0.76,
    "risk_level": "high",
    "approval_required": True,
    "parity_status": "passed",
}

TOOL_REGISTRY = [
    {
        "business_action": "request_purchase_order_approval",
        "endpoint": "POST /api/purchase-orders/{po_id}/approval-request",
        "current_status": "registered",
        "validation_status": "passed",
        "registration_approval": "approved",
        "allowed_consumers": [
            "UiPath API Workflow",
            "optional LangChain/LangGraph wrapper",
        ],
        "execution_mode": "API",
    },
    {
        "business_action": "resolve_vendor_info_missing",
        "current_status": "not_generated",
        "validation_status": "not_started",
        "registration_approval": "not_requested",
        "execution_mode": "RPA",
    },
    {
        "business_action": "handle_inventory_shortage",
        "current_status": "roadmap",
        "validation_status": "roadmap",
        "registration_approval": "not_requested",
        "execution_mode": "RPA",
    },
]

REGISTERED_TOOLS: list[dict[str, object]] = []


def case_id_for_writeback(po_id: str, case_id: str | None) -> str:
    return case_id or (f"CASE-{po_id}" if po_id else "CASE-UNKNOWN")


def record_rpa_writeback_memory(
    *,
    po_id: str,
    result: dict[str, Any],
    before_status: str | None,
    case_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    resolved_case_id = case_id_for_writeback(po_id, case_id)
    try:
        record_execution_trace(
            resolved_case_id,
            {
                "case_id": resolved_case_id,
                "po_id": po_id,
                "business_action": "request_purchase_order_approval",
                "before_state": {"status": before_status} if before_status else None,
                "after_state": {
                    "status": result.get("status"),
                    "audit_log_created": result.get("audit_log_created"),
                },
                "status": result.get("status"),
                "audit_log_created": result.get("audit_log_created"),
                "side_effects": result.get("side_effects", []),
                "event_trace_id": result.get("event_trace_id"),
                "source_endpoint": "/purchase-orders/{po_id}/request-approval",
            },
            execution_mode="RPA",
            source_service="mock-legacy-erp",
            correlation_id=correlation_id,
        )
    except Exception as exc:  # pragma: no cover - covered via monkeypatch test
        memory_logger.warning("Automation Memory RPA write-back failed: %s", exc)


class ModernizationTaskRequest(BaseModel):
    case_id: str
    plan_id: str
    tool_name: str
    business_action: str
    approval_status: str
    approved_by: str
    target_service: str
    proposed_endpoint: str


class ToolRegistrationRequest(BaseModel):
    case_id: str
    tool_name: str
    business_action: str
    approval_status: str
    approved_by: str
    validation_status: str
    readiness_score: float
    modernization_task_id: str
    source_execution_mode: str
    target_execution_mode: str
    api_endpoint: str


def api_readiness_payload() -> dict[str, object]:
    raw_score = (
        0.30 * READINESS_FACTORS["frequency"]
        + 0.30 * READINESS_FACTORS["business_value"]
        + 0.25 * READINESS_FACTORS["field_stability"]
        + 0.15 * READINESS_FACTORS["ui_fragility"]
    )
    return {
        **READINESS_FACTORS,
        "api_readiness_score": round(raw_score, 4),
        "formula_score": round(raw_score * 100),
        "final_score": 86,
        "score_formula": (
            "0.30 * frequency + 0.30 * business_value + "
            "0.25 * field_stability + 0.15 * ui_fragility"
        ),
        "risk_note": (
            "risk_level controls approval and validation strictness; "
            "it does not directly reduce the readiness score."
        ),
    }


def tool_registry_payload() -> dict[str, object]:
    registered_tool_names = {
        str(tool.get("tool_name") or tool.get("business_action"))
        for tool in REGISTERED_TOOLS
    }
    tools = []
    for tool in TOOL_REGISTRY:
        business_action = str(tool["business_action"])
        merged = dict(tool)
        if business_action in registered_tool_names:
            merged["current_status"] = "registered"
            merged["validation_status"] = "VALIDATION_PASSED"
            merged["registration_approval"] = "APPROVED"
            merged["execution_mode"] = "API"
        tools.append(merged)
    return {
        "registry_scope": "local_demo_trusted_tool_evidence",
        "governance_owner": "UiPath",
        "registered_tools": REGISTERED_TOOLS,
        "tools": tools,
    }


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "mock-legacy-erp"}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/purchase-orders", status_code=302)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse)
def login(username: str = Form(...), password: str = Form(...)) -> RedirectResponse:
    if username and password:
        return RedirectResponse(url="/purchase-orders", status_code=303)
    raise HTTPException(status_code=400, detail="Username and password are required")


@app.get("/purchase-orders", response_class=HTMLResponse)
def purchase_orders(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "purchase_orders.html",
        {"purchase_orders": fetch_purchase_orders()},
    )


@app.get("/purchase-orders/{po_id}", response_class=HTMLResponse)
def purchase_order_detail(request: Request, po_id: str) -> HTMLResponse:
    po = fetch_purchase_order(po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return templates.TemplateResponse(
        request,
        "purchase_order_detail.html",
        {"po": po, "audit_logs": fetch_audit_logs_for_po(po_id)},
    )


@app.get("/api/demo/cases")
def demo_cases() -> dict[str, object]:
    return {
        "strategy_hint": "modernization_value",
        "orchestration_owner": "UiPath",
        "cases": DEMO_CASES,
    }


@app.get("/api/demo/rpa-traces/{po_id}")
def rpa_side_effect_trace(po_id: str) -> dict[str, object]:
    trace = fetch_side_effect_trace(po_id)
    if trace is not None:
        return trace
    return {
        "po_id": po_id,
        "status": "NOT_OBSERVED",
        "execution_mode": "RPA",
        "audit_created": False,
        "side_effects": [],
        "expected_side_effect_signature": APPROVAL_SIDE_EFFECTS,
        "event_trace_id": None,
    }


@app.get("/api/demo/cases/next")
def next_demo_case(strategy: str = "modernization_value") -> dict[str, object]:
    if strategy != "modernization_value":
        raise HTTPException(status_code=400, detail="Unsupported demo strategy")
    return {
        "selected_case_id": "CASE-001",
        "selected_po_id": "PO-1001",
        "business_action": "request_purchase_order_approval",
        "strategy": strategy,
        "selection_score": 0.92,
        "selection_reason": (
            "Highest modernization value: frequent budget approval exception "
            "with deterministic RPA write-back and API facade candidate."
        ),
    }


@app.get("/case-dashboard", response_class=HTMLResponse)
def case_dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "case_dashboard.html",
        {"cases": CASE_DASHBOARD},
    )


@app.get("/case-timeline/{case_id}", response_class=HTMLResponse)
def case_timeline(request: Request, case_id: str) -> HTMLResponse:
    if case_id != "CASE-001":
        raise HTTPException(status_code=404, detail="Demo timeline not found")
    return templates.TemplateResponse(
        request,
        "case_timeline.html",
        {"case_id": case_id, "timeline": CASE_001_TIMELINE},
    )


@app.get("/api/demo/cases/{case_id}/timeline")
def case_timeline_json(case_id: str) -> dict[str, object]:
    if case_id != "CASE-001":
        raise HTTPException(status_code=404, detail="Demo timeline not found")
    return {
        "case_id": case_id,
        "timeline": [
            {"sequence": index + 1, "event": item["label"], "status": "complete"}
            for index, item in enumerate(CASE_001_TIMELINE)
        ],
        "orchestration_owner": "UiPath",
        "scope": "local_demo_evidence",
    }


@app.get("/api-readiness-scorecard", response_class=HTMLResponse)
def api_readiness_scorecard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "api_readiness_scorecard.html",
        {"scorecard": api_readiness_payload()},
    )


@app.get("/api/demo/api-readiness-scorecard")
def api_readiness_scorecard_json() -> dict[str, object]:
    return api_readiness_payload()


@app.get("/tool-registry", response_class=HTMLResponse)
def tool_registry(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "tool_registry.html",
        tool_registry_payload(),
    )


@app.get("/api/demo/tool-registry")
def tool_registry_json() -> dict[str, object]:
    return tool_registry_payload()


@app.post("/api/demo/modernization/tasks")
def create_modernization_task(payload: ModernizationTaskRequest) -> dict[str, object]:
    if payload.approval_status != "APPROVED_BY_AUTOMATION_OWNER":
        raise HTTPException(status_code=400, detail="Plan is not approved")
    return {
        "task_id": "MOD-TASK-001",
        "plan_id": payload.plan_id,
        "status": "READY_FOR_CODEX_DRAFT_PR",
        "suggested_branch": "codex/request-purchase-order-approval-api",
        "suggested_pr_title": (
            "Add API facade for purchase order approval request"
        ),
        "codex_prompt_path": (
            "generated-artifacts/request_purchase_order_approval/"
            "codex-pr-prompt.md"
        ),
        "required_tests": [
            "contract_test",
            "business_rule_test",
            "rpa_api_parity_check",
        ],
    }


@app.post("/api/demo/tool-registry/register")
def register_tool(payload: ToolRegistrationRequest) -> dict[str, object]:
    if payload.approval_status != "APPROVED":
        raise HTTPException(status_code=400, detail="Tool approval is not APPROVED")
    if payload.validation_status != "VALIDATION_PASSED":
        raise HTTPException(status_code=400, detail="Validation has not passed")
    registration = {
        "registration_id": "tool-reg-001",
        "tool_name": payload.tool_name,
        "business_action": payload.business_action,
        "trusted_tool_registered": True,
        "approval_status": payload.approval_status,
        "target_execution_mode": payload.target_execution_mode,
        "api_endpoint": payload.api_endpoint,
        "case_id": payload.case_id,
        "modernization_task_id": payload.modernization_task_id,
    }
    REGISTERED_TOOLS[:] = [
        tool
        for tool in REGISTERED_TOOLS
        if tool.get("tool_name") != payload.tool_name
    ]
    REGISTERED_TOOLS.append(registration)
    return registration


@app.post("/api/demo/reset")
def reset_demo_data() -> dict[str, object]:
    init_db()
    REGISTERED_TOOLS.clear()
    return {
        "status": "reset",
        "scope": "local_demo_utility",
        "service": "mock-legacy-erp",
        "purchase_orders": ["PO-1001", "PO-1002", "PO-1003"],
        "note": "UiPath remains responsible for demo case orchestration.",
    }


@app.post("/purchase-orders/{po_id}/request-approval", response_class=HTMLResponse)
def request_purchase_order_approval(
    request: Request,
    po_id: str,
    approval_reason: str = Form(...),
    manager_id: str = Form(...),
    case_id: str | None = Form(None),
    correlation_id: str | None = Form(None),
) -> HTMLResponse:
    before_po = fetch_purchase_order(po_id)
    result = request_approval(
        po_id=po_id,
        approval_reason=approval_reason,
        manager_id=manager_id,
        execution_mode="RPA",
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    record_rpa_writeback_memory(
        po_id=po_id,
        result=result,
        before_status=before_po["status"] if before_po else None,
        case_id=case_id,
        correlation_id=correlation_id,
    )
    return templates.TemplateResponse(
        request,
        "writeback_result.html",
        {"result": result},
    )


@app.get("/audit-logs", response_class=HTMLResponse)
def audit_logs(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "audit_logs.html",
        {"audit_logs": fetch_audit_logs()},
    )
