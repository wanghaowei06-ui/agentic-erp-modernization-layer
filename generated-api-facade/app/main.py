from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .db import init_db, request_approval

app = FastAPI(title="Generated Purchase Order API Facade", version="0.1.0")
BASE_DIR = Path(__file__).resolve().parent.parent


class ApprovalRequest(BaseModel):
    approval_reason: str
    manager_id: str
    source_case_id: str


class ApprovalResponse(BaseModel):
    po_id: str
    status: str
    audit_log_created: bool
    execution_mode: str
    source_case_id: str


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
    return ApprovalResponse(**result)


@app.get("/openapi.yaml", include_in_schema=False)
def openapi_yaml() -> FileResponse:
    return FileResponse(BASE_DIR / "openapi.yaml", media_type="application/yaml")
