from __future__ import annotations

import httpx


def request_purchase_order_approval(
    po_id: str,
    approval_reason: str,
    manager_id: str,
    source_case_id: str,
    base_url: str = "http://localhost:8002",
) -> dict:
    """Optional client wrapper for the API facade, not a workflow orchestrator."""
    response = httpx.post(
        f"{base_url}/api/purchase-orders/{po_id}/approval-request",
        json={
            "approval_reason": approval_reason,
            "manager_id": manager_id,
            "source_case_id": source_case_id,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
