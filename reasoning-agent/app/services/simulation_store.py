"""In-memory simulation queue store.

This module owns the module-level mutable simulation queue state and the
pure helper functions that read/write it. It is a pure extraction from
``app.main`` — no behavior, status machine, field names, or response
shapes have changed.

State ownership:
  - ``SIMULATION_QUEUE`` is the single source of truth for the in-memory
    simulation queue. It is a module-level dict so that Python's
    module-singleton semantics preserve the previous behavior of
    ``_SIMULATION_QUEUE`` in ``app.main`` (all importers see the same
    object). Do NOT replace this with per-call state or a database.

Status machine (must not change):
  pending -> in_progress -> completed | failed
  - Only ``in_progress`` cases can transition to ``completed``/``failed``.
  - ``claim_simulation_case`` performs ``pending -> in_progress``.
  - ``completed``/``failed`` are terminal.

Safety contract:
  - These helpers do NOT write Run Memory, create proposals, call Codex,
    modify XAML, deploy APIs, or register trusted capabilities.
  - ``claim_simulation_case`` raises ``HTTPException(404)`` for unknown
    ids — this is intentional and asserted on by tests.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


# ===========================================================================
# Module-level state — single source of truth for the in-memory queue.
# ===========================================================================
SIMULATION_QUEUE: dict[str, Any] = {"cases": [], "reset_at": None}


# Default 10 mixed cases generated on reset. Do not change scenario mix or
# po_ids — tests assert on PO-SIM-001..010 distribution.
SIMULATION_DEFAULT_CASES: list[dict[str, Any]] = [
    {"po_id": "PO-SIM-001", "case_type": "normal", "scenario": "normal", "amount": 5000, "budget_limit": 10000, "vendor_id": "V-S1", "vendor_info_complete": True, "inventory_available": True, "erp_status": "Normal", "raw_exception_text": "", "business_remarks": "Routine office purchase. No exception noted."},
    {"po_id": "PO-SIM-002", "case_type": "normal", "scenario": "normal", "amount": 3000, "budget_limit": 10000, "vendor_id": "V-S2", "vendor_info_complete": True, "inventory_available": True, "erp_status": "Normal", "raw_exception_text": "", "business_remarks": "Routine office purchase. No exception noted."},
    {"po_id": "PO-SIM-003", "case_type": "exception", "scenario": "budget_exceeded", "amount": 18000, "budget_limit": 10000, "vendor_id": "V-S3", "vendor_info_complete": True, "inventory_available": True, "erp_status": "Exception", "raw_exception_text": "Amount exceeds approved budget limit", "business_remarks": "Q4 customer delivery is at risk. Finance asks whether this should be approved due to strategic account impact."},
    {"po_id": "PO-SIM-004", "case_type": "normal", "scenario": "normal", "amount": 4500, "budget_limit": 10000, "vendor_id": "V-S4", "vendor_info_complete": True, "inventory_available": True, "erp_status": "Normal", "raw_exception_text": "", "business_remarks": "Routine office purchase. No exception noted."},
    {"po_id": "PO-SIM-005", "case_type": "exception", "scenario": "vendor_info_missing", "amount": 6000, "budget_limit": 10000, "vendor_id": None, "vendor_info_complete": False, "inventory_available": True, "erp_status": "Exception", "raw_exception_text": "Vendor information missing", "business_remarks": "Buyer left a note: vendor tax profile was requested last week but is not yet attached."},
    {"po_id": "PO-SIM-006", "case_type": "exception", "scenario": "budget_exceeded", "amount": 15000, "budget_limit": 10000, "vendor_id": "V-S6", "vendor_info_complete": True, "inventory_available": True, "erp_status": "Exception", "raw_exception_text": "Amount exceeds approved budget limit", "business_remarks": "Q4 customer delivery is at risk. Finance asks whether this should be approved due to strategic account impact."},
    {"po_id": "PO-SIM-007", "case_type": "normal", "scenario": "normal", "amount": 2000, "budget_limit": 10000, "vendor_id": "V-S7", "vendor_info_complete": True, "inventory_available": True, "erp_status": "Normal", "raw_exception_text": "", "business_remarks": "Routine office purchase. No exception noted."},
    {"po_id": "PO-SIM-008", "case_type": "exception", "scenario": "budget_exceeded", "amount": 12000, "budget_limit": 10000, "vendor_id": "V-S8", "vendor_info_complete": True, "inventory_available": True, "erp_status": "Exception", "raw_exception_text": "Amount exceeds approved budget limit", "business_remarks": "Q4 customer delivery is at risk. Finance asks whether this should be approved due to strategic account impact."},
    {"po_id": "PO-SIM-009", "case_type": "exception", "scenario": "inventory_shortage", "amount": 8500, "budget_limit": 10000, "vendor_id": "V-S9", "vendor_info_complete": True, "inventory_available": False, "erp_status": "Exception", "raw_exception_text": "Inventory shortage", "business_remarks": "Operations note: substitute parts may be available but require supply chain review."},
    {"po_id": "PO-SIM-010", "case_type": "ambiguous", "scenario": "ambiguous", "amount": 9500, "budget_limit": 10000, "vendor_id": "V-S10", "vendor_info_complete": True, "inventory_available": True, "erp_status": "PendingReview", "raw_exception_text": "Needs business attention before processing.", "business_remarks": "Requester says this order supports a renewal opportunity, but the business justification is incomplete."},
]


def reset_simulation_queue() -> None:
    """Reset the simulation queue to the default 10 mixed cases."""
    from memory.run_memory import _utc_iso

    now = _utc_iso()
    cases = []
    for i, default_case in enumerate(SIMULATION_DEFAULT_CASES, start=1):
        case = dict(default_case)
        case["simulation_case_id"] = f"SIM-{i:03d}"
        case["case_id"] = f"CASE-SIM-{i:03d}"
        case["status"] = "pending"
        case["enqueued_at"] = now
        case["started_at"] = None
        case["completed_at"] = None
        case["run_id"] = None
        case["result"] = None
        case["final_route"] = None
        case["policy_decision"] = None
        case["memory_commit"] = None
        case["last_action"] = None
        cases.append(case)
    SIMULATION_QUEUE["cases"] = cases
    SIMULATION_QUEUE["reset_at"] = now


def simulation_state() -> dict[str, Any]:
    """Return the current simulation queue state.

    Each case includes: status, run_id, result, final_route, policy_decision,
    memory_commit, started_at, completed_at.
    """
    cases = SIMULATION_QUEUE.get("cases", [])
    pending = [c for c in cases if c["status"] == "pending"]
    in_progress = [c for c in cases if c["status"] == "in_progress"]
    completed = [c for c in cases if c["status"] == "completed"]
    failed = [c for c in cases if c["status"] == "failed"]
    return {
        "total": len(cases),
        "pending": len(pending),
        "in_progress": len(in_progress),
        "completed": len(completed),
        "failed": len(failed),
        "reset_at": SIMULATION_QUEUE.get("reset_at"),
        "cases": cases,
    }


def find_simulation_case(simulation_case_id: str) -> dict[str, Any] | None:
    """Find a simulation case by simulation_case_id. Returns None if not found."""
    for c in SIMULATION_QUEUE.get("cases", []):
        if c.get("simulation_case_id") == simulation_case_id:
            return c
    return None


def claim_simulation_case(simulation_case_id: str) -> dict[str, Any]:
    """Claim a simulation case: transition pending -> in_progress.

    This is the structural fix so that opening an ERP detail page
    (GET /erp/work-queue/{id}) or calling POST /simulation/cases/{id}/claim
    automatically moves the case to in_progress, without relying on
    GET /simulation/cases/next.

    Rules:
      - pending -> in_progress (claimed=True)
      - in_progress -> no change (claimed=False, idempotent)
      - completed / failed -> no change (claimed=False, terminal state preserved)
      - unknown id -> raises HTTP 404
    """
    from memory.run_memory import _utc_iso

    case = find_simulation_case(simulation_case_id)
    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation case not found: {simulation_case_id}",
        )
    current_status = case.get("status", "pending")
    if current_status == "pending":
        case["status"] = "in_progress"
        case["started_at"] = _utc_iso()
        return {
            "simulation_case_id": simulation_case_id,
            "status": "in_progress",
            "claimed": True,
            "previous_status": "pending",
        }
    return {
        "simulation_case_id": simulation_case_id,
        "status": current_status,
        "claimed": False,
        "previous_status": current_status,
    }


def build_simulation_summary() -> dict[str, Any]:
    """Build a simulation queue summary for the evidence snapshot.

    Returns counts (pending/in_progress/completed/failed) and a list of
    completed/failed cases with their run_ids.
    """
    state = simulation_state()
    completed_cases = []
    for c in state["cases"]:
        if c["status"] in ("completed", "failed"):
            completed_cases.append({
                "simulation_case_id": c.get("simulation_case_id"),
                "case_id": c.get("case_id"),
                "po_id": c.get("po_id"),
                "status": c["status"],
                "run_id": c.get("run_id"),
                "result": c.get("result"),
                "final_route": c.get("final_route"),
                "policy_decision": c.get("policy_decision"),
                "memory_commit": c.get("memory_commit"),
                "completed_at": c.get("completed_at"),
            })
    return {
        "pending": state["pending"],
        "in_progress": state["in_progress"],
        "completed": state["completed"],
        "failed": state.get("failed", 0),
        "total": state["total"],
        "reset_at": state.get("reset_at"),
        "completed_cases": completed_cases,
    }
