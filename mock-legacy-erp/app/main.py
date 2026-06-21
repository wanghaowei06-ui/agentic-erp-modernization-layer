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
