from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.automation_memory.repository import record_human_approval

from .db import create_approval_task, init_db

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


class ApprovalTaskResponse(BaseModel):
    po_id: str
    task_id: str
    status: str
    business_action: str
    process_signature: str
    audit_log_created: bool
    source_case_id: str
    business_side_effects: list[str]
    evidence_run_ids: list[str]
    observed_count: int


def case_id_for(po_id: str, payload: ApprovalRequest, response: ApprovalTaskResponse) -> str:
    return payload.case_id or response.source_case_id or f"CASE-{po_id}" or "CASE-UNKNOWN"


def approval_task_memory_payload(
    po_id: str,
    payload: ApprovalRequest,
    response: ApprovalTaskResponse,
) -> dict[str, Any]:
    case_id = case_id_for(po_id, payload, response)
    return {
        "case_id": case_id,
        "po_id": response.po_id,
        "task_id": response.task_id,
        "business_action": response.business_action,
        "process_signature": response.process_signature,
        "execution_mode": "HUMAN_APPROVAL",
        "status": response.status,
        "request_summary": {
            "po_id": po_id,
            "case_id": case_id,
            "requested_by": payload.manager_id,
            "approval_reason": payload.approval_reason,
            "correlation_id": payload.correlation_id,
        },
        "response_summary": {
            "po_id": response.po_id,
            "task_id": response.task_id,
            "status": response.status,
            "audit_log_created": response.audit_log_created,
            "source_case_id": response.source_case_id,
            "observed_count": response.observed_count,
        },
        "before_state": None,
        "after_state": {
            "approval_task_status": response.status,
            "audit_log_created": response.audit_log_created,
        },
        "audit_log_created": response.audit_log_created,
        "business_side_effects": response.business_side_effects,
        "evidence_run_ids": response.evidence_run_ids,
        "observed_count": response.observed_count,
        "source_endpoint": "/api/purchase-orders/{po_id}/approval-request",
    }


def record_approval_task_memory(
    po_id: str,
    payload: ApprovalRequest,
    response: ApprovalTaskResponse,
) -> None:
    try:
        record_human_approval(
            case_id_for(po_id, payload, response),
            approval_task_memory_payload(po_id, payload, response),
            source_service="generated-api-facade",
            correlation_id=payload.correlation_id or response.task_id,
        )
    except Exception as exc:  # pragma: no cover - exercised by monkeypatch test
        memory_logger.warning("Automation Memory approval task write failed: %s", exc)


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "generated-api-facade"}


@app.post(
    "/api/purchase-orders/{po_id}/approval-request",
    response_model=ApprovalTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def request_purchase_order_approval(
    po_id: str,
    payload: ApprovalRequest,
) -> ApprovalTaskResponse:
    result = create_approval_task(
        po_id=po_id,
        approval_reason=payload.approval_reason,
        manager_id=payload.manager_id,
        source_case_id=payload.source_case_id,
        correlation_id=payload.correlation_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    response = ApprovalTaskResponse(**result)
    record_approval_task_memory(po_id, payload, response)
    return response


@app.get("/openapi.yaml", include_in_schema=False)
def openapi_yaml() -> FileResponse:
    return FileResponse(BASE_DIR / "openapi.yaml", media_type="application/yaml")
