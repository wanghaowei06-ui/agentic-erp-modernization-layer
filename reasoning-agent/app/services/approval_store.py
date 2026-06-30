"""In-memory human approval task store.

This module owns the module-level mutable approval task state and the
pure helper functions that read/append it. It is a pure extraction from
``app.main`` — no behavior, status machine, field names, or response
shapes have changed.

State ownership:
  - ``APPROVAL_TASKS`` is the single source of truth for in-memory
    approval tasks. It is a module-level dict so Python's module-singleton
    semantics preserve the previous behavior of ``_APPROVAL_TASKS`` in
    ``app.main`` (all importers see the same object). Do NOT replace this
    with per-call state or a database.

Approval status machine (must not change):
  PENDING -> APPROVED_PENDING_ERP_WRITEBACK   (via /approve)
  PENDING -> REJECTED                          (via /reject)
  APPROVED_PENDING_ERP_WRITEBACK -> ERP_WRITEBACK_IN_PROGRESS
                                      (via /mark-writeback-started)
  ERP_WRITEBACK_IN_PROGRESS -> ERP_WRITEBACK_COMPLETED
                                      (via /mark-writeback-completed)
  PENDING is the only state that can transition via approve/reject.

Safety contract:
  - ``append_approval_event_to_run_memory`` and
    ``append_erp_writeback_event_to_run_memory`` are best-effort: they
    never raise — Run Memory write failure must not block the approval
    response.
  - These helpers do NOT call Codex, modify XAML, deploy APIs, or
    register trusted capabilities.

Approved-status set (used for the "approved" count in summaries — must
include all APPROVED_* and ERP_WRITEBACK_* variants):
  APPROVED, APPROVED_PENDING_ERP_WRITEBACK, ERP_WRITEBACK_IN_PROGRESS,
  ERP_WRITEBACK_COMPLETED.
"""
from __future__ import annotations

from typing import Any


# ===========================================================================
# Module-level state — single source of truth for in-memory approval tasks.
# ===========================================================================
APPROVAL_TASKS: dict[str, dict[str, Any]] = {}


# Statuses counted as "approved" in the approvals summary. Do not change —
# /demo/evidence-snapshot and /monitoring/live-data assert on this.
APPROVED_STATUSES = {
    "APPROVED",
    "APPROVED_PENDING_ERP_WRITEBACK",
    "ERP_WRITEBACK_IN_PROGRESS",
    "ERP_WRITEBACK_COMPLETED",
}


def generate_approval_id() -> str:
    """Generate a unique approval ID: APR-YYYYMMDD-NNN."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    date_stamp = now.strftime("%Y%m%d")
    existing = [k for k in APPROVAL_TASKS if k.startswith(f"APR-{date_stamp}-")]
    seq = len(existing) + 1
    return f"APR-{date_stamp}-{seq:03d}"


def list_approvals() -> list[dict[str, Any]]:
    """Return all approval tasks sorted by created_at descending."""
    return sorted(
        APPROVAL_TASKS.values(),
        key=lambda t: t.get("created_at", ""),
        reverse=True,
    )


def pending_approval_count() -> int:
    """Return the number of PENDING approval tasks."""
    return sum(1 for t in APPROVAL_TASKS.values() if t.get("status") == "PENDING")


def append_approval_event_to_run_memory(
    approval: dict[str, Any],
    event_type: str,
    decision: str,
    approver: str,
    comment: str,
) -> dict[str, Any]:
    """Append a HUMAN_APPROVAL_COMPLETED / REJECTED event to Run Memory.

    Returns a dict with the append result or error info. Never raises —
    Run Memory write failure should not block the approval response.
    """
    run_id = approval.get("run_id")
    if not run_id:
        return {"appended": False, "reason": "no run_id associated with approval"}

    try:
        from memory.run_memory import append_event

        record = append_event(
            run_id,
            event_type=event_type,
            case_id=approval.get("case_id"),
            po_id=approval.get("po_id"),
            stage="HUMAN_APPROVAL",
            status=decision.lower(),
            payload={
                "approval_id": approval.get("approval_id"),
                "decision": decision,
                "approver": approver,
                "comment": comment,
                "amount": approval.get("amount"),
                "budget_limit": approval.get("budget_limit"),
            },
        )
        return {"appended": True, "run_id": run_id, "event_type": event_type, "occurred_at": record.get("occurred_at")}
    except Exception as exc:
        return {"appended": False, "reason": str(exc)}


def append_erp_writeback_event_to_run_memory(
    approval: dict[str, Any],
    event_type: str,
    erp_action: str,
    robot_id: str,
) -> dict[str, Any]:
    """Best-effort append of an ERP_WRITEBACK_* event to Run Memory.

    Never raises — returns ``{"appended": False, "reason": "..."}`` on failure.
    """
    run_id = approval.get("run_id")
    if not run_id:
        return {"appended": False, "reason": "no run_id on approval task"}
    try:
        from memory.run_memory import append_event
        append_event(
            run_id=run_id,
            event_type=event_type,
            case_id=approval.get("case_id", ""),
            po_id=approval.get("po_id", ""),
            stage="ERP_WRITEBACK",
            status="COMPLETED",
            payload={
                "approval_id": approval.get("approval_id"),
                "case_id": approval.get("case_id"),
                "po_id": approval.get("po_id"),
                "simulation_case_id": approval.get("simulation_case_id"),
                "erp_action": erp_action,
                "robot_id": robot_id,
                "decision": approval.get("decision"),
                "approver": approval.get("approver"),
            },
        )
        return {
            "appended": True,
            "run_id": run_id,
            "event_type": event_type,
            "artifact_path": f"memory/runs/{run_id}/raw/uipath_execution_events.jsonl",
        }
    except Exception as exc:
        return {"appended": False, "reason": str(exc)}


def build_approvals_summary() -> dict[str, Any]:
    """Build an approvals summary for the evidence snapshot.

    Returns counts (pending/approved/rejected/total) and a list of
    recent approval tasks. Approved variants (APPROVED_PENDING_ERP_WRITEBACK,
    ERP_WRITEBACK_IN_PROGRESS, ERP_WRITEBACK_COMPLETED) are counted as "approved".
    """
    tasks = list_approvals()
    pending = sum(1 for t in tasks if t.get("status") == "PENDING")
    approved = sum(1 for t in tasks if t.get("status") in APPROVED_STATUSES)
    rejected = sum(1 for t in tasks if t.get("status") == "REJECTED")
    recent = [
        {
            "approval_id": t.get("approval_id"),
            "case_id": t.get("case_id"),
            "po_id": t.get("po_id"),
            "run_id": t.get("run_id"),
            "status": t.get("status"),
            "decision": t.get("decision"),
            "approver": t.get("approver"),
            "created_at": t.get("created_at"),
            "approved_at": t.get("approved_at"),
        }
        for t in tasks[:10]
    ]
    return {
        "pending": pending,
        "approved": approved,
        "rejected": rejected,
        "total": len(tasks),
        "recent": recent,
    }
