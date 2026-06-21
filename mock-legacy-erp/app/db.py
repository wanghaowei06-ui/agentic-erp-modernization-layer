from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "legacy_erp.db"


SEED_PURCHASE_ORDERS = [
    {
        "po_id": "PO-1001",
        "amount": 18000,
        "budget_limit": 10000,
        "vendor_id": "V-203",
        "vendor_info_complete": 1,
        "inventory_available": 1,
        "status": "Exception",
        "exception_text": "Amount exceeds approved budget limit",
        "last_action": "",
    },
    {
        "po_id": "PO-1002",
        "amount": 6000,
        "budget_limit": 10000,
        "vendor_id": None,
        "vendor_info_complete": 0,
        "inventory_available": 1,
        "status": "Exception",
        "exception_text": "Vendor information missing",
        "last_action": "",
    },
    {
        "po_id": "PO-1003",
        "amount": 8500,
        "budget_limit": 10000,
        "vendor_id": "V-118",
        "vendor_info_complete": 1,
        "inventory_available": 0,
        "status": "Exception",
        "exception_text": "Inventory shortage",
        "last_action": "",
    },
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
                vendor_id TEXT,
                vendor_info_complete INTEGER NOT NULL,
                inventory_available INTEGER NOT NULL,
                status TEXT NOT NULL,
                exception_text TEXT NOT NULL,
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
                approval_reason TEXT,
                manager_id TEXT,
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
                    po_id, amount, budget_limit, vendor_id, vendor_info_complete,
                    inventory_available, status, exception_text, last_action
                )
                VALUES (
                    :po_id, :amount, :budget_limit, :vendor_id, :vendor_info_complete,
                    :inventory_available, :status, :exception_text, :last_action
                )
                ON CONFLICT(po_id) DO UPDATE SET
                    amount = excluded.amount,
                    budget_limit = excluded.budget_limit,
                    vendor_id = excluded.vendor_id,
                    vendor_info_complete = excluded.vendor_info_complete,
                    inventory_available = excluded.inventory_available,
                    status = excluded.status,
                    exception_text = excluded.exception_text,
                    last_action = excluded.last_action
                """,
                po,
            )


def fetch_purchase_orders() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM purchase_orders ORDER BY po_id"
        ).fetchall()


def fetch_purchase_order(po_id: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM purchase_orders WHERE po_id = ?",
            (po_id,),
        ).fetchone()


def request_approval(
    po_id: str,
    approval_reason: str,
    manager_id: str,
    execution_mode: str = "RPA",
) -> dict[str, Any] | None:
    with get_connection() as conn:
        po = conn.execute(
            "SELECT * FROM purchase_orders WHERE po_id = ?",
            (po_id,),
        ).fetchone()
        if po is None:
            return None

        conn.execute(
            """
            UPDATE purchase_orders
            SET status = ?, last_action = ?
            WHERE po_id = ?
            """,
            ("PENDING_MANAGER_APPROVAL", "approval_requested", po_id),
        )
        cursor = conn.execute(
            """
            INSERT INTO audit_logs (
                po_id, action, approval_reason, manager_id, execution_mode
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                po_id,
                "approval_requested",
                approval_reason,
                manager_id,
                execution_mode,
            ),
        )
        return {
            "po_id": po_id,
            "status": "PENDING_MANAGER_APPROVAL",
            "last_action": "approval_requested",
            "execution_mode": execution_mode,
            "audit_log_created": True,
            "audit_log_id": cursor.lastrowid,
        }


def fetch_audit_logs() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC"
        ).fetchall()
