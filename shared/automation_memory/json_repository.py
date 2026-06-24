from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .event_types import MemoryEventType
from .schemas import CapabilityRecord, MemoryEvent

DEFAULT_MEMORY_DIR = "memory-data"
REPO_ROOT = Path(__file__).resolve().parents[2]
EVENTS_FILE = "events.jsonl"
CAPABILITIES_FILE = "capabilities.json"

AGENT_DECISION_EVENT_TYPES = {
    MemoryEventType.TRIAGE_COMPLETED,
    MemoryEventType.CAPABILITY_LOOKUP_COMPLETED,
    MemoryEventType.READINESS_ASSESSED,
}


class AutomationMemoryJsonError(RuntimeError):
    pass


def memory_dir(data_dir: str | Path | None = None) -> Path:
    configured = data_dir or os.getenv("AUTOMATION_MEMORY_DIR") or DEFAULT_MEMORY_DIR
    path = Path(configured)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def ensure_memory_dir(data_dir: str | Path | None = None) -> Path:
    root = memory_dir(data_dir)
    (root / "case_snapshots").mkdir(parents=True, exist_ok=True)
    return root


def _events_path(data_dir: str | Path | None = None) -> Path:
    return ensure_memory_dir(data_dir) / EVENTS_FILE


def _capabilities_path(data_dir: str | Path | None = None) -> Path:
    return ensure_memory_dir(data_dir) / CAPABILITIES_FILE


def append_event(event: MemoryEvent, data_dir: str | Path | None = None) -> MemoryEvent:
    path = _events_path(data_dir)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(event.model_dump_json() + "\n")
    return event


def read_events(data_dir: str | Path | None = None) -> list[MemoryEvent]:
    path = _events_path(data_dir)
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


def write_capabilities(
    capabilities: list[CapabilityRecord],
    data_dir: str | Path | None = None,
) -> list[CapabilityRecord]:
    path = _capabilities_path(data_dir)
    payload = [capability.model_dump() for capability in capabilities]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return capabilities


def read_capabilities(data_dir: str | Path | None = None) -> list[CapabilityRecord]:
    path = _capabilities_path(data_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AutomationMemoryJsonError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise AutomationMemoryJsonError(f"{path} must contain a JSON list")
    try:
        return [CapabilityRecord.model_validate(item) for item in raw]
    except ValueError as exc:
        raise AutomationMemoryJsonError(
            f"Invalid capability record in {path}: {exc}"
        ) from exc


def upsert_capability(
    capability: CapabilityRecord,
    data_dir: str | Path | None = None,
) -> CapabilityRecord:
    capabilities = [
        existing
        for existing in read_capabilities(data_dir)
        if existing.capability_id != capability.capability_id
    ]
    capabilities.append(capability)
    write_capabilities(capabilities, data_dir)
    return capability


def find_capability(
    business_action: str,
    data_dir: str | Path | None = None,
) -> CapabilityRecord | None:
    for capability in read_capabilities(data_dir):
        if (
            capability.business_action == business_action
            and capability.status == "trusted"
            and capability.validation_status == "passed"
        ):
            return capability
    return None


def query_case_timeline(
    case_id: str,
    data_dir: str | Path | None = None,
) -> list[MemoryEvent]:
    events = [event for event in read_events(data_dir) if event.case_id == case_id]
    return sorted(events, key=lambda event: event.created_at)


def query_case_decisions(
    case_id: str,
    data_dir: str | Path | None = None,
) -> list[MemoryEvent]:
    return [
        event
        for event in query_case_timeline(case_id, data_dir)
        if MemoryEventType(str(event.event_type)) in AGENT_DECISION_EVENT_TYPES
    ]


def query_gaps(data_dir: str | Path | None = None) -> list[MemoryEvent]:
    events = read_events(data_dir)
    return [
        event
        for event in events
        if str(event.event_type) == MemoryEventType.CAPABILITY_GAP_RECORDED.value
    ]


def event_payload(event: MemoryEvent) -> dict[str, Any]:
    return event.model_dump()
