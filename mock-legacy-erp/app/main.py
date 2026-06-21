from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .db import (
    fetch_audit_logs,
    fetch_purchase_order,
    fetch_purchase_orders,
    init_db,
    request_approval,
)

app = FastAPI(title="Mock Legacy ERP UI", version="0.1.0")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


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
    {"id": "timeline-case-created", "label": "Case Created"},
    {"id": "timeline-rpa-extracted", "label": "RPA Extracted ERP Fields"},
    {"id": "timeline-agent-classified", "label": "Agent Classified: budget_exceeded"},
    {"id": "timeline-case-routed", "label": "Case Routed: WAITING_FOR_HUMAN_APPROVAL"},
    {"id": "timeline-human-approved", "label": "Human Approved"},
    {"id": "timeline-rpa-writeback", "label": "RPA Write-back Completed"},
    {"id": "timeline-api-candidate", "label": "API Candidate Generated"},
    {"id": "timeline-validation-passed", "label": "Validation Passed"},
    {"id": "timeline-tool-registered", "label": "Trusted Tool Registered"},
    {"id": "timeline-api-mode-executed", "label": "API Mode Executed"},
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


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "mock-legacy-erp"}


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/purchase-orders", status_code=302)


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
    return templates.TemplateResponse(request, "purchase_order_detail.html", {"po": po})


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
        {"tools": TOOL_REGISTRY},
    )


@app.get("/api/demo/tool-registry")
def tool_registry_json() -> dict[str, object]:
    return {
        "registry_scope": "local_demo_trusted_tool_evidence",
        "governance_owner": "UiPath",
        "tools": TOOL_REGISTRY,
    }


@app.post("/api/demo/reset")
def reset_demo_data() -> dict[str, object]:
    init_db()
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
) -> HTMLResponse:
    result = request_approval(
        po_id=po_id,
        approval_reason=approval_reason,
        manager_id=manager_id,
        execution_mode="RPA",
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
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
