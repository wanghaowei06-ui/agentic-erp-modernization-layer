"""Real Run Memory — directory-structured store for each UiPath execution.

This module implements the new Run Memory data model (Task 1 of the
"Real Run Memory + Trajectory Dashboard + Capability Evolution" upgrade).

Layout under ``memory/``::

    memory/
      runs/
        {run_id}/
          raw/
            uipath_execution_events.jsonl   # append-only event stream
            rpa_click_trace.json
            rpa_selector_trace.json
            rpa_extracted_fields.json
            http_calls.jsonl                # append-only
            agent_input_output.json
            human_approval.json
            validation_response.json
            generated_api_response.json
            errors.jsonl                    # append-only
          normalized/
            case_state.json
            case_timeline.json
            business_action.json
            side_effects_signature.json
            process_signature.json
          summary/
            case_run_summary.json
            post_run_memory_summary.json
          evolution/
            capability_evolution_decision.json
            pattern_update.json
      cases/
        {case_id}/
          case_state.json
          timeline.json
          latest_run_id.txt
          related_runs.json
      patterns/
        {process_signature}.json
      proposals/
        {proposal_id}.json

Design rules (PRD constraints):
- ``run_id`` is unique, formatted ``RUN-YYYYMMDD-NNN``.
- Files under ``raw/`` are append-only for ``*.jsonl`` and never overwritten
  by seed scripts. Other raw artifacts may be overwritten as "latest" but
  always carry ``updated_at``.
- ``memory/data`` legacy files are preserved; this new structure coexists.
- LangGraph MemorySaver is NOT the audit source — this Structured Run Memory
  is the system of record.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_ROOT = REPO_ROOT / "memory"

# Lock guarding run_id sequence allocation (process-wide).
_RUN_ID_LOCK = threading.Lock()

# Key event types that should also update normalized/case_timeline.json.
TIMELINE_EVENT_TYPES = {
    "CASE_CREATED",
    "CASE_RUN_STARTED",
    "RPA_EXTRACTED",
    "TRIAGE_COMPLETED",
    "HUMAN_APPROVAL_COMPLETED",
    "RPA_WRITEBACK_COMPLETED",
    "API_EXECUTION_COMPLETED",
    "VALIDATION_COMPLETED",
    "CAPABILITY_REGISTERED",
    "CAPABILITY_GAP_RECORDED",
    "RUN_COMPLETED",
    "RUN_FAILED",
    "MODERNIZATION_READINESS_ASSESSED",
    "MODERNIZATION_PLAN_GENERATED",
    "EVOLUTION_DECISION_RECORDED",
}

# Artifact types supported by the /artifacts endpoint.
ARTIFACT_TYPES = {
    "rpa_extracted_fields": "rpa_extracted_fields.json",
    # UiPath-facing alias used by the Windows Main.xaml. It writes the same
    # canonical file so existing summary/dashboard derivations keep working.
    "erp_extracted_fields": "rpa_extracted_fields.json",
    "rpa_click_trace": "rpa_click_trace.json",
    "rpa_selector_trace": "rpa_selector_trace.json",
    "http_call": "http_calls.jsonl",
    "triage_agent_io": "agent_input_output.json",
    "agent_route_response": "agent_route_response.json",
    "policy_gate_response": "policy_gate_response.json",
    "company_context_snapshot": "company_context_snapshot.json",
    "route_plan": "route_plan.json",
    "agent_reasoning_summary": "agent_reasoning_summary.json",
    "llm_validation_proof": "llm_validation_proof.json",
    "policy_gate": "policy_gate.json",
    "selected_erp_action": "selected_erp_action.json",
    "approval_task": "approval_task.json",
    "final_branch_result": "final_branch_result.json",
    "branch_result": "final_branch_result.json",
    "erp_ui_action": "erp_ui_actions.jsonl",
    "human_approval": "human_approval.json",
    "rpa_writeback_result": "rpa_writeback_result.json",
    "validation_response": "validation_response.json",
    "modernization_readiness": "modernization_readiness.json",
    "modernization_plan": "modernization_plan.json",
    "generated_api_response": "generated_api_response.json",
    "error": "errors.jsonl",
}

# Artifacts stored as append-only JSONL streams.
APPEND_ONLY_ARTIFACTS = {"http_call", "erp_ui_action", "error"}

# Default schema version stamped on normalized artifacts.
SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def memory_root() -> Path:
    """Return the root ``memory/`` directory (override via env if needed)."""
    override = os.getenv("RUN_MEMORY_ROOT")
    if override:
        path = Path(override)
        return path if path.is_absolute() else REPO_ROOT / path
    return MEMORY_ROOT


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def runs_root() -> Path:
    return _ensure(memory_root() / "runs")


def cases_root() -> Path:
    return _ensure(memory_root() / "cases")


def patterns_root() -> Path:
    return _ensure(memory_root() / "patterns")


def proposals_root() -> Path:
    return _ensure(memory_root() / "proposals")


def run_dir(run_id: str) -> Path:
    return runs_root() / run_id


def run_raw_dir(run_id: str) -> Path:
    return _ensure(run_dir(run_id) / "raw")


def run_normalized_dir(run_id: str) -> Path:
    return _ensure(run_dir(run_id) / "normalized")


def run_summary_dir(run_id: str) -> Path:
    return _ensure(run_dir(run_id) / "summary")


def run_evolution_dir(run_id: str) -> Path:
    return _ensure(run_dir(run_id) / "evolution")


def case_dir(case_id: str) -> Path:
    return _ensure(cases_root() / case_id)


def pattern_path(process_signature: str) -> Path:
    safe = _safe_filename(process_signature)
    return patterns_root() / f"{safe}.json"


def proposal_path(proposal_id: str) -> Path:
    safe = _safe_filename(proposal_id)
    return proposals_root() / f"{safe}.json"


def _safe_filename(name: str) -> str:
    """Make ``name`` safe for use as a single path component."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return cleaned or "unknown"


# ---------------------------------------------------------------------------
# run_id generation
# ---------------------------------------------------------------------------
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _date_stamp() -> str:
    return _utc_now().strftime("%Y%m%d")


def _next_run_sequence(date_stamp: str) -> int:
    """Return the next sequence number for ``date_stamp`` (1-based)."""
    runs = runs_root()
    prefix = f"RUN-{date_stamp}-"
    max_seq = 0
    if runs.exists():
        for entry in runs.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name.startswith(prefix):
                suffix = name[len(prefix):]
                if suffix.isdigit():
                    max_seq = max(max_seq, int(suffix))
    return max_seq + 1


def generate_run_id() -> str:
    """Generate and **reserve** a unique ``RUN-YYYYMMDD-NNN`` identifier.

    The run directory is created atomically inside the lock so that
    successive calls always return distinct IDs (the directory scan in
    ``_next_run_sequence`` observes the previously reserved directory).
    Sequence numbers are process-wide monotonic per UTC date; if the date
    rolls over while the service is running, the sequence restarts at 1.
    """
    with _RUN_ID_LOCK:
        date_stamp = _date_stamp()
        seq = _next_run_sequence(date_stamp)
        run_id = f"RUN-{date_stamp}-{seq:03d}"
        # Atomically reserve the directory so the next call sees it.
        # mkdir(exist_ok=False) protects against unlikely races with other
        # processes; we bump the sequence and retry on conflict.
        while True:
            try:
                run_dir(run_id).mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                seq += 1
                run_id = f"RUN-{date_stamp}-{seq:03d}"
        return run_id


# ---------------------------------------------------------------------------
# JSON / JSONL helpers (local to run memory)
# ---------------------------------------------------------------------------
def _utc_iso() -> str:
    return _utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return path


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _append_jsonl(path: Path, record: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
    return records


# ---------------------------------------------------------------------------
# Run lifecycle — start / events / artifacts / complete
# ---------------------------------------------------------------------------
def start_run(
    *,
    case_id: str,
    po_id: str | None,
    workflow_name: str | None,
    source: str = "uipath",
    demo_mode: bool = False,
) -> dict[str, Any]:
    """Initialize a new run directory and write the CASE_RUN_STARTED event."""
    run_id = generate_run_id()
    raw = run_raw_dir(run_id)
    # Touch the canonical raw files so the structure is observable immediately.
    (raw / "uipath_execution_events.jsonl").touch(exist_ok=True)

    started_event = {
        "event_type": "CASE_RUN_STARTED",
        "run_id": run_id,
        "case_id": case_id,
        "po_id": po_id,
        "workflow_name": workflow_name,
        "source": source,
        "demo_mode": demo_mode,
        "started_at": _utc_iso(),
    }
    _append_jsonl(raw / "uipath_execution_events.jsonl", started_event)

    # cases/{case_id}/related_runs.json
    _record_case_run(case_id, run_id, started_event)

    # normalized/case_state.json (initial)
    normalized = run_normalized_dir(run_id)
    _write_json(
        normalized / "case_state.json",
        {
            "run_id": run_id,
            "case_id": case_id,
            "po_id": po_id,
            "workflow_name": workflow_name,
            "source": source,
            "demo_mode": demo_mode,
            "status": "RUN_STARTED",
            "current_stage": "RUN_STARTED",
            "started_at": started_event["started_at"],
            "schema_version": SCHEMA_VERSION,
        },
    )

    # normalized/case_timeline.json
    _write_json(
        normalized / "case_timeline.json",
        [
            {
                "step": 1,
                "event_type": "CASE_RUN_STARTED",
                "stage": "RUN_STARTED",
                "occurred_at": started_event["started_at"],
                "run_id": run_id,
            }
        ],
    )

    return {
        "run_id": run_id,
        "case_id": case_id,
        "status": "RUN_STARTED",
        "memory_path": str(run_dir(run_id).relative_to(memory_root())),
        "started_at": started_event["started_at"],
    }


def _record_case_run(case_id: str, run_id: str, started_event: dict[str, Any]) -> None:
    cdir = case_dir(case_id)
    related_path = cdir / "related_runs.json"
    related = _read_json(related_path, {"case_id": case_id, "runs": []})
    if not isinstance(related, dict):
        related = {"case_id": case_id, "runs": []}
    runs = related.setdefault("runs", [])
    runs.append(
        {
            "run_id": run_id,
            "started_at": started_event["started_at"],
            "status": started_event["event_type"],
        }
    )
    _write_json(related_path, related)


def append_event(
    run_id: str,
    *,
    event_type: str,
    case_id: str | None = None,
    po_id: str | None = None,
    stage: str | None = None,
    status: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append an event to raw/uipath_execution_events.jsonl.

    Key event types also update normalized/case_timeline.json. Never overwrites
    prior events.
    """
    if not run_dir(run_id).exists():
        raise FileNotFoundError(f"Run directory not found for run_id={run_id}")

    occurred_at = _utc_iso()
    record = {
        "event_type": event_type,
        "run_id": run_id,
        "case_id": case_id,
        "po_id": po_id,
        "stage": stage,
        "status": status,
        "occurred_at": occurred_at,
        "payload": payload or {},
    }
    _append_jsonl(run_raw_dir(run_id) / "uipath_execution_events.jsonl", record)

    # Update case_timeline.json for key event types.
    if event_type in TIMELINE_EVENT_TYPES:
        _append_timeline_step(run_id, event_type, stage, occurred_at)
        if case_id:
            _append_case_timeline(case_id, run_id, event_type, stage, occurred_at)
    return record


def _append_timeline_step(
    run_id: str,
    event_type: str,
    stage: str | None,
    occurred_at: str,
) -> None:
    path = run_normalized_dir(run_id) / "case_timeline.json"
    timeline = _read_json(path, [])
    if not isinstance(timeline, list):
        timeline = []
    timeline.append(
        {
            "step": len(timeline) + 1,
            "event_type": event_type,
            "stage": stage or event_type,
            "occurred_at": occurred_at,
            "run_id": run_id,
        }
    )
    _write_json(path, timeline)


def _append_case_timeline(
    case_id: str,
    run_id: str,
    event_type: str,
    stage: str | None,
    occurred_at: str,
) -> None:
    cdir = case_dir(case_id)
    path = cdir / "timeline.json"
    timeline = _read_json(path, [])
    if not isinstance(timeline, list):
        timeline = []
    timeline.append(
        {
            "step": len(timeline) + 1,
            "event_type": event_type,
            "stage": stage or event_type,
            "occurred_at": occurred_at,
            "run_id": run_id,
        }
    )
    _write_json(path, timeline)


def write_artifact(
    run_id: str,
    *,
    artifact_type: str,
    case_id: str | None = None,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Persist a raw artifact under ``raw/``.

    Append-only artifact types (``http_call``, ``error``) are appended to
    their JSONL streams; other types overwrite the latest file but always
    stamp ``updated_at``.
    """
    if artifact_type not in ARTIFACT_TYPES:
        raise ValueError(f"Unsupported artifact_type: {artifact_type}")
    if not run_dir(run_id).exists():
        raise FileNotFoundError(f"Run directory not found for run_id={run_id}")

    filename = ARTIFACT_TYPES[artifact_type]
    target = run_raw_dir(run_id) / filename
    updated_at = _utc_iso()

    if artifact_type in APPEND_ONLY_ARTIFACTS:
        record = {
            "artifact_type": artifact_type,
            "case_id": case_id,
            "run_id": run_id,
            "occurred_at": updated_at,
            "data": data,
        }
        _append_jsonl(target, record)
        return {
            "run_id": run_id,
            "artifact_type": artifact_type,
            "file": filename,
            "mode": "append",
            "occurred_at": updated_at,
        }

    payload = {
        "artifact_type": artifact_type,
        "case_id": case_id,
        "run_id": run_id,
        "updated_at": updated_at,
        "data": data,
    }
    _write_json(target, payload)
    return {
        "run_id": run_id,
        "artifact_type": artifact_type,
        "file": filename,
        "mode": "overwrite_latest",
        "updated_at": updated_at,
    }


def complete_run(
    run_id: str,
    *,
    case_id: str,
    result: str,
    final_stage: str,
    execution_mode: str,
) -> dict[str, Any]:
    """Write the RUN_COMPLETED event and update case-level state."""
    if not run_dir(run_id).exists():
        raise FileNotFoundError(f"Run directory not found for run_id={run_id}")

    completed_at = _utc_iso()
    record = {
        "event_type": "RUN_COMPLETED",
        "run_id": run_id,
        "case_id": case_id,
        "result": result,
        "final_stage": final_stage,
        "execution_mode": execution_mode,
        "occurred_at": completed_at,
        "payload": {
            "result": result,
            "final_stage": final_stage,
            "execution_mode": execution_mode,
        },
    }
    _append_jsonl(
        run_raw_dir(run_id) / "uipath_execution_events.jsonl", record
    )
    _append_timeline_step(run_id, "RUN_COMPLETED", final_stage, completed_at)
    _append_case_timeline(case_id, run_id, "RUN_COMPLETED", final_stage, completed_at)

    # Update normalized/case_state.json
    normalized = run_normalized_dir(run_id)
    state = _read_json(normalized / "case_state.json", {})
    state.update(
        {
            "run_id": run_id,
            "case_id": case_id,
            "status": "RUN_COMPLETED",
            "current_stage": final_stage,
            "result": result,
            "execution_mode": execution_mode,
            "completed_at": completed_at,
            "schema_version": SCHEMA_VERSION,
        }
    )
    _write_json(normalized / "case_state.json", state)

    # Update cases/{case_id}/case_state.json + latest_run_id.txt
    cdir = case_dir(case_id)
    _write_json(
        cdir / "case_state.json",
        {
            "case_id": case_id,
            "latest_run_id": run_id,
            "status": "RUN_COMPLETED",
            "current_stage": final_stage,
            "result": result,
            "execution_mode": execution_mode,
            "updated_at": completed_at,
        },
    )
    (cdir / "latest_run_id.txt").write_text(run_id, encoding="utf-8")

    # Reflect completion in related_runs.json
    related_path = cdir / "related_runs.json"
    related = _read_json(related_path, {"case_id": case_id, "runs": []})
    if isinstance(related, dict):
        for entry in related.get("runs", []):
            if entry.get("run_id") == run_id:
                entry["status"] = "RUN_COMPLETED"
                entry["completed_at"] = completed_at
                entry["result"] = result
        _write_json(related_path, related)

    return record


# ---------------------------------------------------------------------------
# Normalized derivation + summary generation
# ---------------------------------------------------------------------------
def _load_raw_events(run_id: str) -> list[dict[str, Any]]:
    return _read_jsonl(run_raw_dir(run_id) / "uipath_execution_events.jsonl")


def _load_artifact(run_id: str, artifact_type: str) -> dict[str, Any]:
    filename = ARTIFACT_TYPES[artifact_type]
    return _read_json(run_raw_dir(run_id) / filename, {})


def derive_business_action(
    run_id: str,
    *,
    case_id: str,
    po_id: str | None = None,
) -> str:
    """Derive a stable ``business_action`` from raw artifacts.

    Priority:
      1. ``triage_agent_io`` response business_action.
      2. ``route_plan`` business_action hint.
      3. ``rpa_extracted_fields`` business_action hint.
      4. Fallback derived from workflow_name + 'manual_investigation'.
    """
    triage = _load_artifact(run_id, "triage_agent_io")
    triage_data = triage.get("data", {}) if isinstance(triage, dict) else {}
    response = triage_data.get("response", {}) if isinstance(triage_data, dict) else {}
    action = response.get("business_action")
    if action:
        return str(action)

    route_plan = _load_artifact(run_id, "route_plan")
    route_data = route_plan.get("data", {}) if isinstance(route_plan, dict) else {}
    action = route_data.get("business_action")
    if action:
        return str(action)

    extracted = _load_artifact(run_id, "rpa_extracted_fields")
    extracted_data = extracted.get("data", {}) if isinstance(extracted, dict) else {}
    action = extracted_data.get("business_action")
    if action:
        return str(action)

    return "manual_investigation"


def derive_exception_type(run_id: str) -> str:
    """Derive exception_type from raw artifacts (triage first, then route plan/events)."""
    triage = _load_artifact(run_id, "triage_agent_io")
    triage_data = triage.get("data", {}) if isinstance(triage, dict) else {}
    response = triage_data.get("response", {}) if isinstance(triage_data, dict) else {}
    et = response.get("detected_exception_type")
    if et:
        return str(et)

    route_plan = _load_artifact(run_id, "route_plan")
    route_data = route_plan.get("data", {}) if isinstance(route_plan, dict) else {}
    et = route_data.get("detected_exception_type") or route_data.get("exception_type")
    if et:
        return str(et)

    for event in _load_raw_events(run_id):
        if event.get("event_type") == "RPA_EXTRACTED":
            payload = event.get("payload") or {}
            status = payload.get("status")
            if status and status == "Exception":
                # Heuristic default until triage runs.
                return "budget_exceeded"
    return "unknown_exception"


def derive_side_effects_signature(run_id: str) -> list[str]:
    """Aggregate observed side effects from raw artifacts."""
    sig: list[str] = []
    for source_type in (
        "rpa_writeback_result",
        "generated_api_response",
        "validation_response",
        "selected_erp_action",
        "final_branch_result",
    ):
        artifact = _load_artifact(run_id, source_type)
        data = artifact.get("data", {}) if isinstance(artifact, dict) else {}
        observed = (
            data.get("matched_side_effects")
            or data.get("side_effects_observed")
            or data.get("side_effects_signature")
        )
        if isinstance(observed, list):
            for item in observed:
                if isinstance(item, str) and item not in sig:
                    sig.append(item)
        action_id = data.get("action_id") or data.get("last_action")
        if isinstance(action_id, str) and action_id and action_id not in sig:
            sig.append(action_id)
    return sig


def _family(value: Any, default: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or default


def derive_route_family(run_id: str) -> str:
    route_plan = _load_artifact(run_id, "route_plan")
    route_data = route_plan.get("data", {}) if isinstance(route_plan, dict) else {}
    final_route = route_data.get("final_route") or route_data.get("next_stage")
    if final_route:
        return _family(final_route)

    triage = _load_artifact(run_id, "triage_agent_io")
    triage_data = triage.get("data", {}) if isinstance(triage, dict) else {}
    response = triage_data.get("response", {}) if isinstance(triage_data, dict) else {}
    stage = response.get("final_route") or response.get("next_stage")
    if stage:
        return _family(stage)

    return "route_unknown"


def derive_policy_gate_family(run_id: str) -> str:
    policy = _load_artifact(run_id, "policy_gate")
    policy_data = policy.get("data", {}) if isinstance(policy, dict) else {}
    decision = policy_data.get("policy_decision")
    if decision:
        return _family(decision)

    route_plan = _load_artifact(run_id, "route_plan")
    route_data = route_plan.get("data", {}) if isinstance(route_plan, dict) else {}
    gate = route_data.get("policy_gate") if isinstance(route_data, dict) else {}
    if isinstance(gate, dict) and gate.get("policy_decision"):
        return _family(gate.get("policy_decision"))

    return "policy_unknown"


def derive_side_effects_family(side_effects_signature: list[str]) -> str:
    if not side_effects_signature:
        return "no_side_effects"
    return "+".join(_family(item) for item in sorted(side_effects_signature))


def derive_process_signature(
    business_action: str,
    exception_type: str,
    route_family: str | None = None,
    policy_gate_family: str | None = None,
    side_effects_signature: list[str] | None = None,
) -> str:
    """Process signature groups by action, exception, route, policy and effects."""
    if route_family is None and policy_gate_family is None and side_effects_signature is None:
        return f"{business_action}__{exception_type}"
    if (
        route_family in (None, "unknown", "route_unknown")
        and policy_gate_family in (None, "policy_unknown")
        and not side_effects_signature
    ):
        return f"{business_action}__{exception_type}"
    route = _family(route_family, "route_unknown")
    policy = _family(policy_gate_family, "policy_unknown")
    effects = derive_side_effects_family(side_effects_signature or [])
    return f"{business_action}__{exception_type}__{route}__{policy}__{effects}"


def build_case_run_summary(run_id: str, *, case_id: str) -> dict[str, Any]:
    """Build ``summary/case_run_summary.json`` from raw + normalized."""
    raw_events = _load_raw_events(run_id)
    business_action = derive_business_action(run_id, case_id=case_id)
    exception_type = derive_exception_type(run_id)
    side_effects = derive_side_effects_signature(run_id)
    route_family = derive_route_family(run_id)
    policy_gate_family = derive_policy_gate_family(run_id)
    process_signature = derive_process_signature(
        business_action,
        exception_type,
        route_family,
        policy_gate_family,
        side_effects,
    )
    state = _read_json(run_normalized_dir(run_id) / "case_state.json", {})
    rpa_extracted = _load_artifact(run_id, "rpa_extracted_fields")
    rpa_data = rpa_extracted.get("data", {}) if isinstance(rpa_extracted, dict) else {}
    route_plan = _load_artifact(run_id, "route_plan")
    route_data = route_plan.get("data", {}) if isinstance(route_plan, dict) else {}
    company_context = _load_artifact(run_id, "company_context_snapshot")
    company_context_data = company_context.get("data", {}) if isinstance(company_context, dict) else {}
    agent_reasoning = _load_artifact(run_id, "agent_reasoning_summary")
    agent_reasoning_data = agent_reasoning.get("data", {}) if isinstance(agent_reasoning, dict) else {}
    llm_proof = _load_artifact(run_id, "llm_validation_proof")
    llm_proof_data = llm_proof.get("data", {}) if isinstance(llm_proof, dict) else {}
    policy_gate = _load_artifact(run_id, "policy_gate")
    policy_gate_data = policy_gate.get("data", {}) if isinstance(policy_gate, dict) else {}
    selected_action = _load_artifact(run_id, "selected_erp_action")
    selected_action_data = selected_action.get("data", {}) if isinstance(selected_action, dict) else {}
    final_branch = _load_artifact(run_id, "final_branch_result")
    final_branch_data = final_branch.get("data", {}) if isinstance(final_branch, dict) else {}

    artifact_listing: list[dict[str, Any]] = []
    raw_dir = run_raw_dir(run_id)
    seen_files: set[str] = set()
    for artifact_type, filename in ARTIFACT_TYPES.items():
        if filename in seen_files:
            continue
        seen_files.add(filename)
        path = raw_dir / filename
        if not path.exists():
            continue
        records: int
        if filename.endswith(".jsonl"):
            records = len(_read_jsonl(path))
            display_type = artifact_type
        else:
            payload = _read_json(path, {})
            records = 1 if payload else 0
            display_type = (
                payload.get("artifact_type", artifact_type)
                if isinstance(payload, dict)
                else artifact_type
            )
        artifact_listing.append(
            {
                "artifact_type": display_type,
                "file": filename,
                "records": records,
            }
        )

    return {
        "run_id": run_id,
        "case_id": case_id,
        "business_action": business_action,
        "exception_type": exception_type,
        "process_signature": process_signature,
        "route_family": route_family,
        "policy_gate_family": policy_gate_family,
        "side_effects_signature": side_effects,
        "erp_extracted_fields": rpa_data,
        "business_remarks": rpa_data.get("business_remarks")
        or route_data.get("business_remarks")
        or "",
        "company_context_snapshot": company_context_data,
        "route_plan": route_data,
        "agent_reasoning_summary": agent_reasoning_data.get("agent_reasoning_summary")
        or route_data.get("agent_reasoning_summary")
        or "",
        "llm_validation_proof": llm_proof_data or route_data.get("llm_validation_proof") or {},
        "policy_gate": policy_gate_data or route_data.get("policy_gate") or {},
        "selected_erp_action": selected_action_data or route_data.get("recommended_erp_action") or {},
        "final_branch_result": final_branch_data,
        "event_count": len(raw_events),
        "artifacts": artifact_listing,
        "final_status": state.get("status", "RUN_STARTED"),
        "final_stage": state.get("current_stage"),
        "execution_mode": state.get("execution_mode"),
        "result": state.get("result"),
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_iso(),
    }


def build_post_run_memory_summary(
    run_id: str,
    *,
    case_id: str,
    case_run_summary: dict[str, Any],
    pattern_update: dict[str, Any] | None,
    evolution_decision: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build ``summary/post_run_memory_summary.json``.

    Captures what was written into memory as a result of this run.
    """
    return {
        "run_id": run_id,
        "case_id": case_id,
        "memory_writes": {
            "raw_events_appended": case_run_summary.get("event_count", 0),
            "erp_extracted_fields": case_run_summary.get("erp_extracted_fields", {}),
            "business_remarks": case_run_summary.get("business_remarks", ""),
            "company_context_snapshot": case_run_summary.get("company_context_snapshot", {}),
            "route_plan": case_run_summary.get("route_plan", {}),
            "agent_reasoning_summary": case_run_summary.get("agent_reasoning_summary", ""),
            "llm_validation_proof": case_run_summary.get("llm_validation_proof", {}),
            "policy_gate": case_run_summary.get("policy_gate", {}),
            "selected_erp_action": case_run_summary.get("selected_erp_action", {}),
            "final_branch_result": case_run_summary.get("final_branch_result", {}),
            "normalized_business_action": case_run_summary.get("business_action"),
            "normalized_side_effects_signature": case_run_summary.get(
                "side_effects_signature", []
            ),
            "normalized_process_signature": case_run_summary.get("process_signature"),
        },
        "pattern_update": pattern_update,
        "capability_evolution_decision": evolution_decision,
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_iso(),
    }


# ---------------------------------------------------------------------------
# Run inspection
# ---------------------------------------------------------------------------
def list_raw_artifacts(run_id: str) -> list[dict[str, Any]]:
    raw_dir = run_raw_dir(run_id)
    artifacts: list[dict[str, Any]] = []
    if not raw_dir.exists():
        return artifacts
    seen_files: set[str] = set()
    for artifact_type, filename in ARTIFACT_TYPES.items():
        if filename in seen_files:
            continue
        seen_files.add(filename)
        path = raw_dir / filename
        if not path.exists():
            continue
        if filename.endswith(".jsonl"):
            records = _read_jsonl(path)
            artifacts.append(
                {
                    "artifact_type": artifact_type,
                    "file": filename,
                    "format": "jsonl",
                    "record_count": len(records),
                    "records": records,
                }
            )
        else:
            payload = _read_json(path, {})
            display_type = (
                payload.get("artifact_type", artifact_type)
                if isinstance(payload, dict)
                else artifact_type
            )
            artifacts.append(
                {
                    "artifact_type": display_type,
                    "file": filename,
                    "format": "json",
                    "record_count": 1 if payload else 0,
                    "data": payload.get("data", payload) if payload else None,
                    "updated_at": payload.get("updated_at") if payload else None,
                }
            )
    return artifacts


def load_run_view(run_id: str) -> dict[str, Any]:
    """Return the full inspectable structure for a run (GET endpoint)."""
    rdir = run_dir(run_id)
    if not rdir.exists():
        raise FileNotFoundError(f"Run directory not found for run_id={run_id}")

    raw_events = _read_jsonl(run_raw_dir(run_id) / "uipath_execution_events.jsonl")
    raw_artifacts = list_raw_artifacts(run_id)
    normalized_dir = run_normalized_dir(run_id)
    summary_dir = run_summary_dir(run_id)
    evolution_dir = run_evolution_dir(run_id)

    def _read_subdir(path: Path) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if not path.exists():
            return result
        for child in path.iterdir():
            if child.is_file() and child.suffix == ".json":
                result[child.stem] = _read_json(child, None)
        return result

    normalized = _read_subdir(normalized_dir)
    summary = _read_subdir(summary_dir)
    evolution = _read_subdir(evolution_dir)

    # Proposal (if referenced by the evolution decision).
    proposal = None
    decision = evolution.get("capability_evolution_decision")
    if isinstance(decision, dict):
        proposal_id = decision.get("proposal_id")
        if proposal_id:
            proposal = _read_json(proposal_path(proposal_id), None)

    return {
        "run_id": run_id,
        "run_path": str(rdir.relative_to(memory_root())),
        "raw": {
            "uipath_execution_events": raw_events,
            "artifacts": raw_artifacts,
        },
        "normalized": normalized,
        "summary": summary,
        "evolution": evolution,
        "proposal": proposal,
    }
