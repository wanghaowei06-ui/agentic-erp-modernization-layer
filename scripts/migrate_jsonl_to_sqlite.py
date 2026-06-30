#!/usr/bin/env python3
"""One-time migration: load legacy ``memory-data/events.jsonl`` into SQLite.

The Automation Memory event store moved from JSONL to SQLite (``events.db``)
to avoid full-table scans on every query. This script reads any existing
``events.jsonl`` and inserts every event into the SQLite store. It is
idempotent: events whose ``event_id`` already exists in SQLite are skipped.

Usage::

    python scripts/migrate_jsonl_to_sqlite.py [--data-dir memory-data]

After a successful migration the JSONL file is preserved (renamed to
``events.jsonl.bak``) so it remains available as an audit artifact. Use
``--keep-source`` to leave the original JSONL untouched.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.automation_memory.json_repository import (  # noqa: E402
    AutomationMemoryJsonError,
    memory_dir,
)
from shared.automation_memory.sqlite_store import (  # noqa: E402
    db_path,
    migrate_from_jsonl,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Memory data directory (defaults to AUTOMATION_MEMORY_DIR or memory-data).",
    )
    parser.add_argument(
        "--keep-source",
        action="store_true",
        help="Do not rename events.jsonl after migration.",
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    source = memory_dir(data_dir) / "events.jsonl"
    if not source.exists():
        print(f"[migrate] No {source} found; nothing to migrate.")
        return 0

    try:
        migrated, db = migrate_from_jsonl(data_dir)
    except AutomationMemoryJsonError as exc:
        print(f"[migrate] ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"[migrate] Migrated {migrated} event(s) into {db}.")
    if not args.keep_source and migrated >= 0:
        backup = source.with_suffix(".jsonl.bak")
        source.rename(backup)
        print(f"[migrate] Renamed source to {backup} (audit artifact).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
