from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.automation_memory.repository import record_execution_trace

from .db import init_db, request_approval

app = FastAPI(title="Generated Purchase Order API Facade", version="0.1.0")
BASE_DIR = Path(__file__).resolve().parent.parent
memory_logger = logging.getLogger("generated-api-facade.memory")
memory_logger.setLevel(logging.INFO)
if not memory_logger.handlers:
    memory_handler = logging.StreamHandler()
    memory_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    memory_logger.addHandler(memory_handler)


class ApprovalRequest(BaseModel):
    approval_reason: str
    manager_id: str
    source_case_id: str
    case_id: str | None = None
    correlation_id: str | None = None


class ApprovalResponse(BaseModel):
    po_id: str
    status: str
    audit_log_created: bool
    execution_mode: str
    source_case_id: str
    side_effects: list[str]
    event_trace_id: str


def case_id_for(po_id: str, payload: ApprovalRequest, response: ApprovalResponse) -> str:
    return payload.case_id or response.source_case_id or f"CASE-{po_id}" or "CASE-UNKNOWN"


def api_execution_memory_payload(
    po_id: str,
    payload: ApprovalRequest,
    response: ApprovalResponse,
) -> dict[str, Any]:
    case_id = case_id_for(po_id, payload, response)
    return {
        "case_id": case_id,
        "po_id": response.po_id,
        "business_action": "request_purchase_order_approval",
        "execution_mode": "API",
        "request_summary": {
            "po_id": po_id,
            "case_id": case_id,
            "requested_by": payload.manager_id,
            "approval_reason": payload.approval_reason,
            "correlation_id": payload.correlation_id,
        },
        "response_summary": {
            "po_id": response.po_id,
            "status": response.status,
            "audit_log_created": response.audit_log_created,
            "execution_mode": response.execution_mode,
            "source_case_id": response.source_case_id,
            "event_trace_id": response.event_trace_id,
        },
        "before_state": None,
        "after_state": {
            "status": response.status,
            "audit_log_created": response.audit_log_created,
        },
        "status": response.status,
        "audit_log_created": response.audit_log_created,
        "side_effects": response.side_effects,
        "event_trace_id": response.event_trace_id,
        "source_endpoint": "/api/purchase-orders/{po_id}/approval-request",
    }


def record_api_execution_memory(
    po_id: str,
    payload: ApprovalRequest,
    response: ApprovalResponse,
) -> None:
    try:
        record_execution_trace(
            case_id_for(po_id, payload, response),
            api_execution_memory_payload(po_id, payload, response),
            execution_mode="API",
            source_service="generated-api-facade",
            correlation_id=payload.correlation_id or response.event_trace_id,
        )
    except Exception as exc:  # pragma: no cover - exercised by monkeypatch test
        memory_logger.warning("Automation Memory API execution write failed: %s", exc)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "generated-api-facade"}


@app.post(
    "/api/purchase-orders/{po_id}/approval-request",
    response_model=ApprovalResponse,
)
def request_purchase_order_approval(
    po_id: str,
    payload: ApprovalRequest,
) -> ApprovalResponse:
    result = request_approval(
        po_id=po_id,
        approval_reason=payload.approval_reason,
        manager_id=payload.manager_id,
        source_case_id=payload.source_case_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    response = ApprovalResponse(**result)
    record_api_execution_memory(po_id, payload, response)
    return response


@app.get("/openapi.yaml", include_in_schema=False)
def openapi_yaml() -> FileResponse:
    return FileResponse(BASE_DIR / "openapi.yaml", media_type="application/yaml")
