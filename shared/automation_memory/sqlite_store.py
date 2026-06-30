from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .json_repository import ensure_memory_dir, memory_dir
from .schemas import MemoryEvent

# SQLite replaces events.jsonl as the primary event store to avoid the full-table
# scan cost of reading every line on each query. JSONL export is retained for
# audit/backup via ``export_events_jsonl``.
EVENTS_DB_FILE = "events.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_events (
    event_id        TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    source_service  TEXT NOT NULL,
    schema_version  TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    correlation_id  TEXT,
    payload         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_events_case_id    ON memory_events(case_id);
CREATE INDEX IF NOT EXISTS idx_memory_events_event_type ON memory_events(event_type);
CREATE INDEX IF NOT EXISTS idx_memory_events_created_at ON memory_events(created_at);
"""


def db_path(data_dir: str | Path | None = None) -> Path:
    return ensure_memory_dir(data_dir) / EVENTS_DB_FILE


def _connect(data_dir: str | Path | None = None) -> sqlite3.Connection:
    path = db_path(data_dir)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(data_dir: str | Path | None = None) -> Path:
    path = db_path(data_dir)
    with _connect(data_dir) as conn:
        conn.executescript(_SCHEMA)
    return path


def _row_to_event(row: sqlite3.Row) -> MemoryEvent:
    return MemoryEvent.model_validate(
        {
            "event_id": row["event_id"],
            "case_id": row["case_id"],
            "event_type": row["event_type"],
            "source_service": row["source_service"],
            "schema_version": row["schema_version"],
            "created_at": row["created_at"],
            "correlation_id": row["correlation_id"],
            "payload": json.loads(row["payload"]),
        }
    )


def insert_event(event: MemoryEvent, data_dir: str | Path | None = None) -> MemoryEvent:
    init_db(data_dir)
    with _connect(data_dir) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO memory_events
                (event_id, case_id, event_type, source_service,
                 schema_version, created_at, correlation_id, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.case_id,
                str(event.event_type),
                event.source_service,
                event.schema_version,
                event.created_at,
                event.correlation_id,
                json.dumps(event.payload, ensure_ascii=False),
            ),
        )
    return event


def select_all_events(data_dir: str | Path | None = None) -> list[MemoryEvent]:
    if not db_path(data_dir).exists():
        return []
    with _connect(data_dir) as conn:
        rows = conn.execute(
            "SELECT * FROM memory_events ORDER BY created_at ASC, rowid ASC"
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def select_events_by_case(
    case_id: str,
    data_dir: str | Path | None = None,
) -> list[MemoryEvent]:
    if not db_path(data_dir).exists():
        return []
    with _connect(data_dir) as conn:
        rows = conn.execute(
            "SELECT * FROM memory_events WHERE case_id = ? "
            "ORDER BY created_at ASC, rowid ASC",
            (case_id,),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def select_events_by_type(
    event_type: str,
    data_dir: str | Path | None = None,
) -> list[MemoryEvent]:
    if not db_path(data_dir).exists():
        return []
    with _connect(data_dir) as conn:
        rows = conn.execute(
            "SELECT * FROM memory_events WHERE event_type = ? "
            "ORDER BY created_at ASC, rowid ASC",
            (event_type,),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def select_decisions_by_case(
    case_id: str,
    decision_types: Iterable[str],
    data_dir: str | Path | None = None,
) -> list[MemoryEvent]:
    types = list(decision_types)
    if not types:
        return []
    if not db_path(data_dir).exists():
        return []
    placeholders = ",".join("?" for _ in types)
    with _connect(data_dir) as conn:
        rows = conn.execute(
            f"SELECT * FROM memory_events WHERE case_id = ? "
            f"AND event_type IN ({placeholders}) "
            f"ORDER BY created_at ASC, rowid ASC",
            (case_id, *types),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def count_events_by_type(
    event_type: str,
    data_dir: str | Path | None = None,
) -> int:
    if not db_path(data_dir).exists():
        return 0
    with _connect(data_dir) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM memory_events WHERE event_type = ?",
            (event_type,),
        ).fetchone()
    return int(row["n"])


def count_gaps_by_business_action(
    business_action: str,
    data_dir: str | Path | None = None,
    exception_type: str | None = None,
) -> int:
    """Count capability-gap events whose payload references ``business_action``.

    Gap payloads store the uncovered action under ``required_business_action``
    (see PRD 18.4). Uses SQLite ``json_extract`` so the count is index-backed
    rather than a Python full-table scan. When ``exception_type`` is supplied,
    the count is further restricted to gaps with that exception type
    (PRD 17.6 ``count_repeated_gaps(gap_type, business_action)``).
    """
    if not db_path(data_dir).exists():
        return 0
    clauses = [
        "event_type = 'CAPABILITY_GAP_RECORDED'",
        "COALESCE("
        "json_extract(payload, '$.required_business_action'), "
        "json_extract(payload, '$.legacy_gap_payload.required_business_action')"
        ") = ?",
    ]
    params: list[Any] = [business_action]
    if exception_type:
        clauses.append(
            "COALESCE("
            "json_extract(payload, '$.exception_type'), "
            "json_extract(payload, '$.detected_exception_type'), "
            "json_extract(payload, '$.legacy_gap_payload.exception_type')"
            ") = ?"
        )
        params.append(exception_type)
    with _connect(data_dir) as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM memory_events WHERE "
            f"{' AND '.join(clauses)}",
            tuple(params),
        ).fetchone()
    return int(row["n"])


def find_similar_cases(
    exception_type: str,
    business_action: str,
    data_dir: str | Path | None = None,
) -> list[MemoryEvent]:
    """Return gap events matching ``exception_type`` AND ``business_action``.

    Implements PRD 17.6 ``find_similar_cases(exception_type, business_action)``.
    Matching is performed on the ``CAPABILITY_GAP_RECORDED`` event payload via
    SQLite ``json_extract`` so it is an indexed lookup on ``event_type`` plus a
    narrow payload filter, not a Python full-table scan. The business action
    and exception type are matched against the common payload variants
    (top-level, ``legacy_gap_payload`` nested, ``detected_exception_type``).
    """
    if not db_path(data_dir).exists():
        return []
    with _connect(data_dir) as conn:
        rows = conn.execute(
            """
            SELECT * FROM memory_events
            WHERE event_type = 'CAPABILITY_GAP_RECORDED'
              AND COALESCE(
                json_extract(payload, '$.required_business_action'),
                json_extract(payload, '$.legacy_gap_payload.required_business_action')
              ) = ?
              AND COALESCE(
                json_extract(payload, '$.exception_type'),
                json_extract(payload, '$.detected_exception_type'),
                json_extract(payload, '$.legacy_gap_payload.exception_type')
              ) = ?
            ORDER BY created_at ASC, rowid ASC
            """,
            (business_action, exception_type),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def export_events_jsonl(
    data_dir: str | Path | None = None,
    target_path: str | Path | None = None,
) -> Path:
    """Export all SQLite events to a JSONL file for audit/backup.

    Defaults to ``events.jsonl`` inside the memory data dir.
    """
    out = Path(target_path) if target_path else memory_dir(data_dir) / "events.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    events = select_all_events(data_dir)
    with out.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(event.model_dump_json() + "\n")
    return out


def _read_jsonl_legacy(
    data_dir: str | Path | None = None,
) -> list[MemoryEvent]:
    """Read a legacy ``events.jsonl`` file for one-time migration to SQLite."""
    from .json_repository import AutomationMemoryJsonError

    path = memory_dir(data_dir) / "events.jsonl"
    if not path.exists():
        return []
    events: list[MemoryEvent] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                events.append(MemoryEvent.model_validate_json(stripped))
            except (json.JSONDecodeError, ValueError) as exc:
                raise AutomationMemoryJsonError(
                    f"Invalid JSONL event in {path} at line {line_number}: {exc}"
                ) from exc
    return events


def migrate_from_jsonl(
    data_dir: str | Path | None = None,
) -> tuple[int, Path]:
    """Migrate existing ``events.jsonl`` rows into the SQLite store.

    Idempotent: events whose ``event_id`` already exists in SQLite are skipped.
    Returns ``(migrated_count, db_path)``.
    """
    events = _read_jsonl_legacy(data_dir)
    init_db(data_dir)
    migrated = 0
    if not events:
        return 0, db_path(data_dir)
    with _connect(data_dir) as conn:
        for event in events:
            cur = conn.execute(
                "INSERT OR IGNORE INTO memory_events "
                "(event_id, case_id, event_type, source_service, "
                " schema_version, created_at, correlation_id, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.case_id,
                    str(event.event_type),
                    event.source_service,
                    event.schema_version,
                    event.created_at,
                    event.correlation_id,
                    json.dumps(event.payload, ensure_ascii=False),
                ),
            )
            migrated += cur.rowcount
    return migrated, db_path(data_dir)
