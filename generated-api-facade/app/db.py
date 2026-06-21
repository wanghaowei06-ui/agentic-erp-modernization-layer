from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "api_facade.db"


SEED_PURCHASE_ORDERS = [
    ("PO-1001", 18000, 10000, "Exception", ""),
    ("PO-1002", 6000, 10000, "Exception", ""),
    ("PO-1003", 8500, 10000, "Exception", ""),
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
        conn.execute("DELETE FROM audit_logs")
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


def request_approval(
    po_id: str,
    approval_reason: str,
    manager_id: str,
    source_case_id: str,
) -> dict[str, Any] | None:
    with get_connection() as conn:
        po = conn.execute(
            "SELECT * FROM purchase_orders WHERE po_id = ?",
            (po_id,),
        ).fetchone()
        if po is None:
            return None

        if po["status"] != "PENDING_MANAGER_APPROVAL":
            conn.execute(
                """
                UPDATE purchase_orders
                SET status = ?, last_action = ?
                WHERE po_id = ?
                """,
                ("PENDING_MANAGER_APPROVAL", "approval_requested", po_id),
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
                    "approval_requested",
                    approval_reason,
                    manager_id,
                    source_case_id,
                    "API",
                ),
            )

        return {
            "po_id": po_id,
            "status": "PENDING_MANAGER_APPROVAL",
            "audit_log_created": True,
            "execution_mode": "API",
            "source_case_id": source_case_id,
        }


def audit_count(po_id: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM audit_logs WHERE po_id = ?",
            (po_id,),
        ).fetchone()
        return int(row["count"])
