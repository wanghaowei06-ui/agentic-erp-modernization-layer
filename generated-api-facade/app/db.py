from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "api_facade.db"

BUSINESS_ACTION = "manual_investigation"
PROCESS_SIGNATURE = (
    "manual_investigation__budget_exceeded__waiting_for_human_approval__"
    "require_human_approval__no_side_effects"
)
EVIDENCE_RUN_IDS = [
    "RUN-20260630-001",
    "RUN-20260630-002",
    "RUN-20260630-003",
    "RUN-20260630-004",
]
OBSERVED_COUNT = 4
APPROVAL_TASK_STATUS = "PENDING_HUMAN_APPROVAL"
NO_BUSINESS_SIDE_EFFECTS: list[str] = []


SEED_PURCHASE_ORDERS = [
    ("PO-1001", 18000, 10000, "Exception", ""),
    ("PO-1002", 6000, 10000, "Exception", ""),
    ("PO-1003", 8500, 10000, "Exception", ""),
    ("PO-1001-API", 18000, 10000, "Exception", ""),
]


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS purchase_orders (
                po_id TEXT PRIMARY KEY,
                amount INTEGER NOT NULL,
                budget_limit INTEGER NOT NULL,
                status TEXT NOT NULL,
                last_action TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                po_id TEXT NOT NULL,
                action TEXT NOT NULL,
                approval_reason TEXT NOT NULL,
                manager_id TEXT NOT NULL,
                source_case_id TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT UNIQUE NOT NULL,
                po_id TEXT NOT NULL,
                business_action TEXT NOT NULL,
                process_signature TEXT NOT NULL,
                status TEXT NOT NULL,
                approval_reason TEXT NOT NULL,
                manager_id TEXT NOT NULL,
                source_case_id TEXT NOT NULL,
                correlation_id TEXT,
                evidence_run_ids TEXT NOT NULL,
                observed_count INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("DELETE FROM audit_logs")
        conn.execute("DELETE FROM approval_tasks")
        for po in SEED_PURCHASE_ORDERS:
            conn.execute(
                """
                INSERT INTO purchase_orders (
                    po_id, amount, budget_limit, status, last_action
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(po_id) DO UPDATE SET
                    amount = excluded.amount,
                    budget_limit = excluded.budget_limit,
                    status = excluded.status,
                    last_action = excluded.last_action
                """,
                po,
            )


def _task_id_for(po_id: str) -> str:
    safe_po_id = "".join(
        character if character.isalnum() else "-"
        for character in po_id.upper()
    ).strip("-")
    return f"TASK-{safe_po_id}-APPROVAL"


def create_approval_task(
    po_id: str,
    approval_reason: str,
    manager_id: str,
    source_case_id: str,
    correlation_id: str | None = None,
) -> dict[str, Any] | None:
    with get_connection() as conn:
        po = conn.execute(
            "SELECT * FROM purchase_orders WHERE po_id = ?",
            (po_id,),
        ).fetchone()
        if po is None:
            return None

        task_id = _task_id_for(po_id)
        task = conn.execute(
            "SELECT * FROM approval_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        created = task is None
        if created:
            conn.execute(
                """
                INSERT INTO approval_tasks (
                    task_id, po_id, business_action, process_signature, status,
                    approval_reason, manager_id, source_case_id, correlation_id,
                    evidence_run_ids, observed_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    po_id,
                    BUSINESS_ACTION,
                    PROCESS_SIGNATURE,
                    APPROVAL_TASK_STATUS,
                    approval_reason,
                    manager_id,
                    source_case_id,
                    correlation_id,
                    ",".join(EVIDENCE_RUN_IDS),
                    OBSERVED_COUNT,
                ),
            )
            conn.execute(
                """
                INSERT INTO audit_logs (
                    po_id, action, approval_reason, manager_id,
                    source_case_id, execution_mode
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    po_id,
                    "human_approval_task_created",
                    approval_reason,
                    manager_id,
                    source_case_id,
                    "HUMAN_APPROVAL",
                ),
            )

        return {
            "po_id": po_id,
            "task_id": task_id,
            "status": APPROVAL_TASK_STATUS,
            "business_action": BUSINESS_ACTION,
            "process_signature": PROCESS_SIGNATURE,
            "audit_log_created": True,
            "source_case_id": source_case_id,
            "business_side_effects": NO_BUSINESS_SIDE_EFFECTS,
            "evidence_run_ids": EVIDENCE_RUN_IDS,
            "observed_count": OBSERVED_COUNT,
            "created": created,
        }


def audit_count(po_id: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM audit_logs WHERE po_id = ?",
            (po_id,),
        ).fetchone()
        return int(row["count"])


def approval_task_count(po_id: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM approval_tasks WHERE po_id = ?",
            (po_id,),
        ).fetchone()
        return int(row["count"])


def purchase_order_status(po_id: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM purchase_orders WHERE po_id = ?",
            (po_id,),
        ).fetchone()
        return str(row["status"]) if row else None
