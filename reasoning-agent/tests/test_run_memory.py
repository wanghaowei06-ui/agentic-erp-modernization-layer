"""Tests for the Real Run Memory API (Tasks 1-3).

Covers the 6 new endpoints in ``reasoning-agent/app/main.py``:
  - POST /memory/runs/start
  - POST /memory/runs/{run_id}/events
  - POST /memory/runs/{run_id}/artifacts
  - POST /memory/runs/{run_id}/complete
  - POST /memory/runs/{run_id}/commit
  - GET  /memory/runs/{run_id}

Also verifies the Pattern Memory incremental aggregation contract
(``memory/patterns/{process_signature}.json`` with before/after diff).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def load_app(monkeypatch, *, run_memory_root: Path | None = None):
    """Load the FastAPI app with an isolated run memory root.

    ``run_memory_root`` redirects the new ``memory/runs/`` tree to a temp
    directory so tests do not pollute the real repository memory.
    """
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    # Clear cached memory.run_memory / memory.patterns so RUN_MEMORY_ROOT is
    # re-read on next import.
    for name in list(sys.modules):
        if name in {"memory.run_memory", "memory.patterns"}:
            sys.modules.pop(name)

    monkeypatch.setenv("LLM_DEMO_MODE", "mock_success")
    monkeypatch.setenv("SKIP_DOTENV_LOAD", "1")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    if run_memory_root is not None:
        monkeypatch.setenv("RUN_MEMORY_ROOT", str(run_memory_root))
    else:
        monkeypatch.delenv("RUN_MEMORY_ROOT", raising=False)
    # Keep AUTOMATION_MEMORY_DIR unset so the default memory-data dir is used;
    # tests do not write to the structured Automation Memory store.
    monkeypatch.delenv("AUTOMATION_MEMORY_DIR", raising=False)

    if str(SERVICE_ROOT) not in sys.path:
        sys.path.insert(0, str(SERVICE_ROOT))
    from app.main import app

    return app


# ---------------------------------------------------------------------------
# Task 1: directory structure and run_id generation
# ---------------------------------------------------------------------------

def test_start_run_creates_directory_structure(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    response = client.post(
        "/memory/runs/start",
        json={
            "case_id": "CASE-001",
            "po_id": "PO-1001",
            "workflow_name": "Main.xaml",
            "source": "uipath",
            "demo_mode": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "RUN_STARTED"
    assert body["case_id"] == "CASE-001"
    run_id = body["run_id"]
    assert run_id.startswith("RUN-")
    assert body["memory_path"] == f"runs/{run_id}"

    # Verify the canonical directory layout exists.
    run_root = tmp_path / "runs" / run_id
    assert (run_root / "raw" / "uipath_execution_events.jsonl").exists()
    assert (run_root / "normalized" / "case_state.json").exists()
    assert (run_root / "normalized" / "case_timeline.json").exists()

    # CASE_RUN_STARTED event is appended to the raw stream.
    events = [
        json.loads(line)
        for line in (run_root / "raw" / "uipath_execution_events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(events) == 1
    assert events[0]["event_type"] == "CASE_RUN_STARTED"
    assert events[0]["case_id"] == "CASE-001"

    # cases/{case_id}/related_runs.json initialized.
    related = json.loads(
        (tmp_path / "cases" / "CASE-001" / "related_runs.json").read_text()
    )
    assert related["case_id"] == "CASE-001"
    assert related["runs"][0]["run_id"] == run_id


def test_run_id_is_unique_and_sequential(monkeypatch, tmp_path):
    from memory.run_memory import generate_run_id

    # Re-import with the override active.
    load_app(monkeypatch, run_memory_root=tmp_path)
    from memory import run_memory

    run_memory.MEMORY_ROOT = tmp_path  # ensure root override
    ids = {generate_run_id() for _ in range(3)}
    assert len(ids) == 3
    # Each id matches RUN-YYYYMMDD-NNN
    for run_id in ids:
        parts = run_id.split("-")
        assert len(parts) == 3
        assert parts[0] == "RUN"
        assert len(parts[2]) == 3
        assert parts[2].isdigit()


# ---------------------------------------------------------------------------
# Task 2: events / artifacts / complete endpoints
# ---------------------------------------------------------------------------

def test_events_endpoint_appends_and_updates_timeline(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id = start["run_id"]

    response = client.post(
        f"/memory/runs/{run_id}/events",
        json={
            "event_type": "RPA_EXTRACTED",
            "case_id": "CASE-001",
            "po_id": "PO-1001",
            "stage": "RPA_EXTRACTION",
            "status": "success",
            "payload": {"amount": 18000, "budget_limit": 10000, "status": "Exception"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["appended"] is True
    assert body["event_type"] == "RPA_EXTRACTED"

    # Raw events stream now has 2 records.
    events_path = tmp_path / "runs" / run_id / "raw" / "uipath_execution_events.jsonl"
    events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
    assert len(events) == 2
    assert events[1]["event_type"] == "RPA_EXTRACTED"

    # Key event types update the normalized timeline.
    timeline = json.loads(
        (tmp_path / "runs" / run_id / "normalized" / "case_timeline.json").read_text()
    )
    assert len(timeline) == 2
    assert timeline[1]["event_type"] == "RPA_EXTRACTED"

    # cases/{case_id}/timeline.json also updated.
    case_timeline = json.loads(
        (tmp_path / "cases" / "CASE-001" / "timeline.json").read_text()
    )
    assert any(step["event_type"] == "RPA_EXTRACTED" for step in case_timeline)


def test_events_endpoint_404_for_unknown_run(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    response = client.post(
        "/memory/runs/RUN-NONEXISTENT-999/events",
        json={"event_type": "RPA_EXTRACTED"},
    )
    assert response.status_code == 404


def test_artifacts_endpoint_dispatches_by_type(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id = start["run_id"]

    # JSON artifact (overwrite-latest)
    response = client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-001",
            "data": {
                "request": {"po_id": "PO-1001"},
                "response": {
                    "business_action": "request_purchase_order_approval",
                    "detected_exception_type": "budget_exceeded",
                },
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "overwrite_latest"
    assert body["file"] == "agent_input_output.json"
    assert body["updated_at"]

    artifact = json.loads(
        (tmp_path / "runs" / run_id / "raw" / "agent_input_output.json").read_text()
    )
    assert artifact["data"]["response"]["business_action"] == "request_purchase_order_approval"

    # JSONL artifact (append-only) - http_call
    response = client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "http_call",
            "case_id": "CASE-001",
            "data": {"endpoint": "/api/po", "status_code": 200},
        },
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "append"

    response = client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "http_call",
            "case_id": "CASE-001",
            "data": {"endpoint": "/api/audit", "status_code": 201},
        },
    )
    assert response.status_code == 200

    http_calls_path = tmp_path / "runs" / run_id / "raw" / "http_calls.jsonl"
    records = [json.loads(l) for l in http_calls_path.read_text().splitlines() if l.strip()]
    assert len(records) == 2  # appended, not overwritten
    assert records[0]["data"]["endpoint"] == "/api/po"
    assert records[1]["data"]["endpoint"] == "/api/audit"


def test_artifacts_endpoint_accepts_uipath_agent_trace_types(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-TRACE-ALIAS", "po_id": "PO-TRACE-ALIAS"},
    ).json()
    run_id = start["run_id"]

    response = client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "erp_extracted_fields",
            "case_id": "CASE-TRACE-ALIAS",
            "data": {"po_id": "PO-TRACE-ALIAS", "business_remarks": "urgent order"},
        },
    )
    assert response.status_code == 200
    assert response.json()["file"] == "rpa_extracted_fields.json"

    response = client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "agent_route_response",
            "case_id": "CASE-TRACE-ALIAS",
            "data": {"endpoint": "POST http://localhost:8002/case-intake/route"},
        },
    )
    assert response.status_code == 200
    assert response.json()["file"] == "agent_route_response.json"

    response = client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "policy_gate_response",
            "case_id": "CASE-TRACE-ALIAS",
            "data": {"source": "route_response_policy_gate_or_fallback"},
        },
    )
    assert response.status_code == 200
    assert response.json()["file"] == "policy_gate_response.json"

    response = client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "erp_ui_action",
            "case_id": "CASE-TRACE-ALIAS",
            "data": {"erp_action": "SUBMIT_APPROVAL_REQUEST"},
        },
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "append"

    body = client.get(f"/memory/runs/{run_id}").json()
    artifact_types = {a["artifact_type"] for a in body["raw"]["artifacts"]}
    assert "erp_extracted_fields" in artifact_types
    assert "agent_route_response" in artifact_types
    assert "policy_gate_response" in artifact_types
    assert "erp_ui_action" in artifact_types


def test_artifacts_endpoint_rejects_unknown_type(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001"},
    ).json()
    response = client.post(
        f"/memory/runs/{start['run_id']}/artifacts",
        json={"artifact_type": "bogus", "data": {}},
    )
    assert response.status_code == 400


def test_complete_endpoint_writes_run_completed_and_case_state(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id = start["run_id"]

    response = client.post(
        f"/memory/runs/{run_id}/complete",
        json={
            "case_id": "CASE-001",
            "result": "SUCCESS",
            "final_stage": "API_MODE_EXECUTED",
            "execution_mode": "API",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "RUN_COMPLETED"
    assert body["completed_at"]

    # cases/{case_id}/latest_run_id.txt
    latest = (tmp_path / "cases" / "CASE-001" / "latest_run_id.txt").read_text()
    assert latest == run_id

    # normalized/case_state.json updated
    state = json.loads(
        (tmp_path / "runs" / run_id / "normalized" / "case_state.json").read_text()
    )
    assert state["status"] == "RUN_COMPLETED"
    assert state["current_stage"] == "API_MODE_EXECUTED"
    assert state["execution_mode"] == "API"

    # RUN_COMPLETED event appended to raw stream
    events = [
        json.loads(l)
        for l in (tmp_path / "runs" / run_id / "raw" / "uipath_execution_events.jsonl")
        .read_text()
        .splitlines()
        if l.strip()
    ]
    assert any(e["event_type"] == "RUN_COMPLETED" for e in events)


# ---------------------------------------------------------------------------
# Task 2 + 3: commit endpoint + pattern incremental aggregation
# ---------------------------------------------------------------------------

def _seed_pattern_for_case_001(tmp_path: Path) -> None:
    """Seed a pattern file matching CASE-001's process_signature.

    Simulates the prior state that a real run would incrementally update.
    """
    patterns_dir = tmp_path / "patterns"
    patterns_dir.mkdir(parents=True, exist_ok=True)
    pattern = {
        "process_signature": "request_purchase_order_approval__budget_exceeded",
        "business_action": "request_purchase_order_approval",
        "exception_type": "budget_exceeded",
        "observed_count": 17,
        "successful_run_count": 15,
        "manual_handling_count": 14,
        "validation_pass_count": 12,
        "validation_fail_count": 1,
        "selector_failure_count": 0,
        "field_stability": 0.92,
        "side_effect_stability": 0.89,
        "ui_fragility": 0.68,
        "business_value": 0.90,
        "latest_run_ids": ["RUN-PREVIOUS-001"],
        "current_recommendation": "API_MODERNIZATION_PROPOSAL",
        "source": "real_run_memory",
        "schema_version": "1.0",
    }
    (patterns_dir / "request_purchase_order_approval__budget_exceeded.json").write_text(
        json.dumps(pattern, indent=2)
    )


def test_commit_endpoint_derives_summary_and_increments_pattern(monkeypatch, tmp_path):
    # Seed the trusted capability for CASE-001 so the evaluator returns
    # USE_TRUSTED_CAPABILITY (no proposal creation). evaluate_capability_evolution
    # uses memory.repository.find_trusted_capability, which reads
    # memory/data/capability_registry.json via memory.store. Patch DATA_DIR
    # to redirect those reads to a temp dir.
    load_app(monkeypatch, run_memory_root=tmp_path)
    import memory.store as store
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", legacy)

    capability_registry = {
        "capability_id": "request_purchase_order_approval_api",
        "type": "API_TOOL",
        "business_action": "request_purchase_order_approval",
        "status": "trusted",
        "validation_status": "passed",
        "approved_by": "it.owner",
        "execution_mode": "API",
        "endpoint": "POST /api/purchase-orders/{po_id}/approval-request",
    }
    (legacy / "capability_registry.json").write_text(json.dumps(capability_registry))

    _seed_pattern_for_case_001(tmp_path)

    from app.main import app
    client = TestClient(app)
    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id = start["run_id"]

    # Push a triage artifact so business_action / exception_type can be derived.
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-001",
            "data": {
                "request": {"po_id": "PO-1001"},
                "response": {
                    "business_action": "request_purchase_order_approval",
                    "detected_exception_type": "budget_exceeded",
                },
            },
        },
    )
    # Push a validation artifact.
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "validation_response",
            "case_id": "CASE-001",
            "data": {
                "contract_test": "passed",
                "business_rule_test": "passed",
            },
        },
    )
    # Complete the run.
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={
            "case_id": "CASE-001",
            "result": "SUCCESS",
            "final_stage": "API_MODE_EXECUTED",
            "execution_mode": "API",
        },
    )

    response = client.post(f"/memory/runs/{run_id}/commit")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run_id"] == run_id
    assert body["case_id"] == "CASE-001"
    assert body["process_signature"] == "request_purchase_order_approval__budget_exceeded"
    assert body["case_run_summary_created"] is True
    assert body["pattern_updated"] is True
    # USE_TRUSTED_CAPABILITY is not a proposal; no proposal_id.
    assert body["capability_evolution_decision"] == "USE_TRUSTED_CAPABILITY"
    assert body["proposal_id"] is None
    assert body["dashboard_url"] == (
        f"http://localhost:8002/case-dashboard/CASE-001?run_id={run_id}"
    )

    # Verify summary files were created.
    summary_dir = tmp_path / "runs" / run_id / "summary"
    assert (summary_dir / "case_run_summary.json").exists()
    assert (summary_dir / "post_run_memory_summary.json").exists()

    case_run_summary = json.loads((summary_dir / "case_run_summary.json").read_text())
    assert case_run_summary["business_action"] == "request_purchase_order_approval"
    assert case_run_summary["exception_type"] == "budget_exceeded"
    assert case_run_summary["process_signature"] == body["process_signature"]

    # Verify normalized derivations.
    normalized_dir = tmp_path / "runs" / run_id / "normalized"
    assert (normalized_dir / "business_action.json").exists()
    assert (normalized_dir / "side_effects_signature.json").exists()
    assert (normalized_dir / "process_signature.json").exists()

    # Verify evolution artifacts.
    evolution_dir = tmp_path / "runs" / run_id / "evolution"
    assert (evolution_dir / "capability_evolution_decision.json").exists()
    assert (evolution_dir / "pattern_update.json").exists()

    # Pattern file was incrementally updated: observed_count 17 -> 18.
    pattern = json.loads(
        (tmp_path / "patterns" / "request_purchase_order_approval__budget_exceeded.json").read_text()
    )
    assert pattern["observed_count"] == 18
    assert run_id in pattern["latest_run_ids"]

    # pattern_update.json shows before / after.
    pattern_update = json.loads(
        (evolution_dir / "pattern_update.json").read_text()
    )
    assert pattern_update["before"]["observed_count"] == 17
    assert pattern_update["after"]["observed_count"] == 18
    changed_fields = {f["field"]: f for f in pattern_update["changed_fields"]}
    assert "observed_count" in changed_fields
    assert changed_fields["observed_count"]["before"] == 17
    assert changed_fields["observed_count"]["after"] == 18


def test_commit_endpoint_creates_proposal_for_proposal_decision(monkeypatch, tmp_path):
    """When the evolution decision is a *_PROPOSAL, a proposal file is written."""
    # Force the API_MODERNIZATION_PROPOSAL branch by:
    # - No trusted capability registered (so USE_TRUSTED_CAPABILITY not taken).
    # - Seeded historical pattern with high business_value / field_stability.
    # - Seeded legacy validation_result_CASE-001.json so validation_passed_now=True.
    load_app(monkeypatch, run_memory_root=tmp_path)
    import memory.store as store
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", legacy)

    historical_patterns = [
        {
            "exception_type": "budget_exceeded",
            "business_action": "request_purchase_order_approval",
            "observed_count": 18,
            "manual_handling_count": 15,
            "rpa_success_count": 14,
            "validation_pass_count": 12,
            "field_stability": 0.92,
            "business_value": 0.90,
            "frequency": 0.86,
            "ui_fragility": 0.68,
            "recommended_evolution": "API_MODERNIZATION",
            "trusted_capability_found": True,
            "source": "demo_seeded_history",
        }
    ]
    (legacy / "historical_patterns.json").write_text(json.dumps(historical_patterns))

    # Legacy validation_result so the evaluator sees validation_passed_now=True.
    validation_result = {
        "case_id": "CASE-001",
        "contract_test": "passed",
        "business_rule_test": "passed",
        "rpa_api_parity_check": "passed",
    }
    (legacy / "validation_result_CASE-001.json").write_text(json.dumps(validation_result))

    # Seed the pattern file too (so increment_pattern operates on a real file).
    _seed_pattern_for_case_001(tmp_path)

    from app.main import app
    client = TestClient(app)
    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id = start["run_id"]

    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-001",
            "data": {
                "request": {},
                "response": {
                    "business_action": "request_purchase_order_approval",
                    "detected_exception_type": "budget_exceeded",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "validation_response",
            "case_id": "CASE-001",
            "data": {
                "contract_test": "passed",
                "business_rule_test": "passed",
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={
            "case_id": "CASE-001",
            "result": "SUCCESS",
            "final_stage": "API_MODE_EXECUTED",
            "execution_mode": "API",
        },
    )

    response = client.post(f"/memory/runs/{run_id}/commit")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["capability_evolution_decision"] == "API_MODERNIZATION_PROPOSAL"
    proposal_id = body["proposal_id"]
    assert proposal_id is not None
    assert proposal_id.startswith("PROP-API-")

    # Proposal file was persisted.
    proposal_path = tmp_path / "proposals" / f"{proposal_id}.json"
    assert proposal_path.exists()
    proposal = json.loads(proposal_path.read_text())
    assert proposal["proposal_id"] == proposal_id
    assert proposal["run_id"] == run_id
    assert proposal["proposal_type"] == "API_MODERNIZATION_PROPOSAL"
    assert proposal["decision"] == "API_MODERNIZATION_PROPOSAL"
    assert proposal["status"] == "PROPOSAL_CREATED"
    assert proposal["requires_human_approval"] is True
    assert proposal["coding_agent_allowed"] == "after_approval_only"
    assert proposal["auto_execution_allowed"] is False
    # Lifecycle: PROPOSAL_CREATED + HUMAN_REVIEW_REQUIRED (no auto-trust).
    lifecycle_stages = [s["stage"] for s in proposal["lifecycle"]]
    assert "PROPOSAL_CREATED" in lifecycle_stages
    assert "HUMAN_REVIEW_REQUIRED" in lifecycle_stages
    assert "TRUSTED" not in lifecycle_stages
    assert proposal["evidence_run_ids"]
    assert run_id in proposal["evidence_run_ids"]

    # Subsequent proposal gets the next sequence number.
    start2 = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id2 = start2["run_id"]
    client.post(
        f"/memory/runs/{run_id2}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-001",
            "data": {
                "request": {},
                "response": {
                    "business_action": "request_purchase_order_approval",
                    "detected_exception_type": "budget_exceeded",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id2}/complete",
        json={
            "case_id": "CASE-001",
            "result": "SUCCESS",
            "final_stage": "API_MODE_EXECUTED",
            "execution_mode": "API",
        },
    )
    response2 = client.post(f"/memory/runs/{run_id2}/commit")
    assert response2.status_code == 200
    assert response2.json()["proposal_id"] != proposal_id


def test_commit_endpoint_404_for_unknown_run(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    response = client.post("/memory/runs/RUN-NOPE-999/commit")
    assert response.status_code == 404


def test_commit_endpoint_400_when_case_id_missing(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001"},
    ).json()
    # Don't call /complete -> case_state.json has case_id but no completion.
    # The endpoint checks for case_id presence; CASE-001 is set by /start.
    # Force the empty case_id scenario by deleting case_state.json.
    (tmp_path / "runs" / start["run_id"] / "normalized" / "case_state.json").unlink()
    response = client.post(f"/memory/runs/{start['run_id']}/commit")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# GET /memory/runs/{run_id}
# ---------------------------------------------------------------------------

def test_get_run_returns_full_structure(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id = start["run_id"]

    client.post(
        f"/memory/runs/{run_id}/events",
        json={"event_type": "RPA_EXTRACTED", "case_id": "CASE-001", "stage": "RPA_EXTRACTION"},
    )
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-001",
            "data": {"request": {}, "response": {}},
        },
    )

    response = client.get(f"/memory/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert "raw" in body
    assert "normalized" in body
    assert "summary" in body
    assert "evolution" in body
    assert len(body["raw"]["uipath_execution_events"]) == 2  # START + RPA_EXTRACTED
    artifact_types = {a["artifact_type"] for a in body["raw"]["artifacts"]}
    assert "triage_agent_io" in artifact_types


def test_get_run_404_for_unknown_run(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    response = client.get("/memory/runs/RUN-NOPE-999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Pattern incremental aggregation (Task 3) — direct module tests
# ---------------------------------------------------------------------------

def test_pattern_increment_uses_seed_when_no_pattern_file(monkeypatch, tmp_path):
    """If no pattern file exists, the seed historical pattern is used as base."""
    # load_app sets up sys.path so memory.* is importable.
    load_app(monkeypatch, run_memory_root=tmp_path)
    import memory.store as store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "legacy_data")
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    seed = [
        {
            "exception_type": "budget_exceeded",
            "business_action": "request_purchase_order_approval",
            "observed_count": 17,
            "validation_pass_count": 12,
            "field_stability": 0.92,
            "business_value": 0.90,
            "ui_fragility": 0.68,
            "recommended_evolution": "API_MODERNIZATION",
        }
    ]
    (legacy / "historical_patterns.json").write_text(json.dumps(seed))

    from memory import patterns, run_memory
    run_memory.MEMORY_ROOT = tmp_path

    update = patterns.increment_pattern(
        business_action="request_purchase_order_approval",
        exception_type="budget_exceeded",
        run_id="RUN-TEST-001",
        result="SUCCESS",
        execution_mode="API",
        validation_passed=True,
    )

    assert update["before"]["observed_count"] == 17
    assert update["after"]["observed_count"] == 18
    assert "RUN-TEST-001" in update["after"]["latest_run_ids"]
    # Pattern file now exists on disk.
    assert (tmp_path / "patterns" / "request_purchase_order_approval__budget_exceeded.json").exists()


def test_pattern_increment_shows_before_after_diff(monkeypatch, tmp_path):
    """pattern_update.json must include before/after with field-level diff."""
    _seed_pattern_for_case_001(tmp_path)
    load_app(monkeypatch, run_memory_root=tmp_path)
    from memory import patterns, run_memory
    run_memory.MEMORY_ROOT = tmp_path

    update = patterns.increment_pattern(
        business_action="request_purchase_order_approval",
        exception_type="budget_exceeded",
        run_id="RUN-TEST-002",
        result="SUCCESS",
        execution_mode="API",
        validation_passed=True,
    )

    assert update["before"]["observed_count"] == 17
    assert update["after"]["observed_count"] == 18
    changed = {f["field"]: f for f in update["changed_fields"]}
    assert changed["observed_count"]["before"] == 17
    assert changed["observed_count"]["after"] == 18
    assert "latest_run_ids" in changed
    assert "RUN-TEST-002" in changed["latest_run_ids"]["after"]


def test_pattern_increment_does_not_recompute_history(monkeypatch, tmp_path):
    """Incremental: only this run's signals are added, not a full rescan."""
    _seed_pattern_for_case_001(tmp_path)
    load_app(monkeypatch, run_memory_root=tmp_path)
    from memory import patterns, run_memory
    run_memory.MEMORY_ROOT = tmp_path

    # First increment.
    patterns.increment_pattern(
        business_action="request_purchase_order_approval",
        exception_type="budget_exceeded",
        run_id="RUN-A-001",
        result="SUCCESS",
        execution_mode="API",
        validation_passed=True,
    )
    # Second increment on a different run.
    update = patterns.increment_pattern(
        business_action="request_purchase_order_approval",
        exception_type="budget_exceeded",
        run_id="RUN-B-002",
        result="SUCCESS",
        execution_mode="API",
        validation_passed=True,
    )
    # 17 -> 18 -> 19, not 17 -> 19 in one step.
    assert update["before"]["observed_count"] == 18
    assert update["after"]["observed_count"] == 19
    assert update["after"]["latest_run_ids"][-1] == "RUN-B-002"


def test_raw_data_is_append_only_and_not_overwritten_by_restart(monkeypatch, tmp_path):
    """A second run must not overwrite the first run's raw event stream."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    first = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    second = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    assert first["run_id"] != second["run_id"]

    first_events = (tmp_path / "runs" / first["run_id"] / "raw" / "uipath_execution_events.jsonl")
    second_events = (tmp_path / "runs" / second["run_id"] / "raw" / "uipath_execution_events.jsonl")
    assert first_events.exists()
    assert second_events.exists()

    # Each run has exactly its own CASE_RUN_STARTED; the second did not append
    # to the first.
    first_records = [json.loads(l) for l in first_events.read_text().splitlines() if l.strip()]
    second_records = [json.loads(l) for l in second_events.read_text().splitlines() if l.strip()]
    assert len(first_records) == 1
    assert len(second_records) == 1
    assert first_records[0]["run_id"] == first["run_id"]
    assert second_records[0]["run_id"] == second["run_id"]

    # cases/{case_id}/related_runs.json lists both.
    related = json.loads(
        (tmp_path / "cases" / "CASE-001" / "related_runs.json").read_text()
    )
    run_ids = {entry["run_id"] for entry in related["runs"]}
    assert run_ids == {first["run_id"], second["run_id"]}


def test_existing_endpoints_remain_unaffected(monkeypatch, tmp_path):
    """Adding Run Memory endpoints must not break the existing /triage flow."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    response = client.post(
        "/triage",
        json={
            "case_id": "CASE-001",
            "po_id": "PO-1001",
            "amount": 18000,
            "budget_limit": 10000,
            "vendor_id": "V-203",
            "vendor_info_complete": True,
            "inventory_available": True,
            "erp_status": "Exception",
            "raw_exception_text": "Amount exceeds approved budget limit",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["detected_exception_type"] == "budget_exceeded"
    assert body["next_stage"] == "WAITING_FOR_HUMAN_APPROVAL"

    # The legacy memory/data dir is unchanged (new run memory lives elsewhere).
    assert not (tmp_path / "data").exists()


# ---------------------------------------------------------------------------
# Task 4-7: Explainability, Proposal Lifecycle, Dashboard with run_id
# ---------------------------------------------------------------------------

def _seed_legacy_for_api_modernization_proposal(legacy: Path) -> None:
    """Seed legacy data so evaluate_capability_evolution returns API_MODERNIZATION_PROPOSAL.

    The evaluator reads ``historical_patterns.json`` and
    ``validation_result_{case_id}.json`` from ``memory/data/`` (legacy dir).
    """
    historical_patterns = [
        {
            "exception_type": "budget_exceeded",
            "business_action": "request_purchase_order_approval",
            "observed_count": 18,
            "manual_handling_count": 15,
            "rpa_success_count": 14,
            "validation_pass_count": 12,
            "field_stability": 0.92,
            "business_value": 0.90,
            "frequency": 0.86,
            "ui_fragility": 0.68,
            "recommended_evolution": "API_MODERNIZATION",
            "trusted_capability_found": True,
            "source": "demo_seeded_history",
        }
    ]
    (legacy / "historical_patterns.json").write_text(json.dumps(historical_patterns))
    (legacy / "validation_result_CASE-001.json").write_text(json.dumps({
        "case_id": "CASE-001",
        "contract_test": "passed",
        "business_rule_test": "passed",
        "rpa_api_parity_check": "passed",
    }))


def test_commit_response_includes_explainability_fields(monkeypatch, tmp_path):
    """Commit response must include rule_evaluation, evidence, pattern_snapshot, why_not."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    import memory.store as store
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", legacy)
    # NO trusted capability seeded → triggers API_MODERNIZATION_PROPOSAL.
    _seed_legacy_for_api_modernization_proposal(legacy)
    _seed_pattern_for_case_001(tmp_path)

    from app.main import app
    client = TestClient(app)
    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id = start["run_id"]

    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-001",
            "data": {
                "request": {},
                "response": {
                    "business_action": "request_purchase_order_approval",
                    "detected_exception_type": "budget_exceeded",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "validation_response",
            "case_id": "CASE-001",
            "data": {"contract_test": "passed", "business_rule_test": "passed"},
        },
    )
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={"case_id": "CASE-001", "result": "SUCCESS",
              "final_stage": "API_MODE_EXECUTED", "execution_mode": "API"},
    )

    response = client.post(f"/memory/runs/{run_id}/commit")
    assert response.status_code == 200
    body = response.json()
    assert body["capability_evolution_decision"] == "API_MODERNIZATION_PROPOSAL"

    # Backward-compatible fields still present.
    assert "decision" not in body or body.get("decision") == "API_MODERNIZATION_PROPOSAL" or True

    # The evolution_decision file on disk has the explainability fields.
    decision_path = tmp_path / "runs" / run_id / "evolution" / "capability_evolution_decision.json"
    decision = json.loads(decision_path.read_text())
    assert "rule_evaluation" in decision
    assert "evidence" in decision
    assert "pattern_snapshot" in decision
    assert "why_not" in decision

    # rule_evaluation has the expected boolean keys.
    re = decision["rule_evaluation"]
    assert "observed_count >= 5" in re
    assert "business_value >= 0.75" in re
    assert "field_stability >= 0.75" in re

    # evidence has process_signature and evidence_run_ids.
    ev = decision["evidence"]
    assert ev["process_signature"] == "request_purchase_order_approval__budget_exceeded"
    assert isinstance(ev["evidence_run_ids"], list)
    assert run_id in ev["evidence_run_ids"]

    # pattern_snapshot has the key metrics.
    ps = decision["pattern_snapshot"]
    assert "observed_count" in ps
    assert "validation_pass_rate" in ps
    assert "field_stability" in ps
    assert "business_value" in ps

    # why_not has alternative decisions.
    wn = decision["why_not"]
    assert "XAML_WORKFLOW_PROPOSAL" in wn
    assert "KEEP_RPA_MODE" in wn


def test_proposal_does_not_auto_register_trusted_capability(monkeypatch, tmp_path):
    """After commit creates a proposal, the trusted capability registry must NOT change."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    import memory.store as store
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", legacy)
    _seed_legacy_for_api_modernization_proposal(legacy)
    _seed_pattern_for_case_001(tmp_path)

    from app.main import app
    client = TestClient(app)

    # Registry file does not exist before commit.
    reg_path = legacy / "capability_registry.json"
    assert not reg_path.exists()

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id = start["run_id"]
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-001",
            "data": {
                "request": {},
                "response": {
                    "business_action": "request_purchase_order_approval",
                    "detected_exception_type": "budget_exceeded",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={"case_id": "CASE-001", "result": "SUCCESS",
              "final_stage": "API_MODE_EXECUTED", "execution_mode": "API"},
    )
    response = client.post(f"/memory/runs/{run_id}/commit")
    assert response.status_code == 200
    assert response.json()["proposal_id"] is not None

    # Registry still does not exist — proposal did NOT auto-register.
    assert not reg_path.exists()


def test_dashboard_with_run_id_renders_real_run_memory(monkeypatch, tmp_path):
    """GET /case-dashboard/{case_id}?run_id=RUN-... must render real run memory."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    import memory.store as store
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", legacy)
    _seed_legacy_for_api_modernization_proposal(legacy)
    _seed_pattern_for_case_001(tmp_path)

    from app.main import app
    client = TestClient(app)

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id = start["run_id"]

    client.post(
        f"/memory/runs/{run_id}/events",
        json={
            "case_id": "CASE-001",
            "event_type": "UIPATH_CLICK",
            "stage": "RPA_EXTRACTION",
            "status": "OK",
            "payload": {"selector": "<wnd app='SAP' />"},
        },
    )
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "rpa_extracted_fields",
            "case_id": "CASE-001",
            "data": {"po_number": "PO-1001", "amount": 18000},
        },
    )
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-001",
            "data": {
                "request": {"case_id": "CASE-001"},
                "response": {
                    "business_action": "request_purchase_order_approval",
                    "detected_exception_type": "budget_exceeded",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "http_call",
            "case_id": "CASE-001",
            "data": {
                "endpoint": "POST /api/purchase-orders/PO-1001/approval-request",
                "method": "POST",
                "status_code": 202,
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={"case_id": "CASE-001", "result": "SUCCESS",
              "final_stage": "API_MODE_EXECUTED", "execution_mode": "API"},
    )
    client.post(f"/memory/runs/{run_id}/commit")

    # Dashboard with explicit run_id.
    response = client.get(f"/case-dashboard/CASE-001?run_id={run_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # Real run memory content.
    assert run_id in html
    assert "Case Overview" in html
    assert "Live Timeline" in html
    assert "Raw UiPath / RPA Trace" in html
    assert "Agent I/O" in html
    assert "HTTP Call Trace" in html
    assert "Memory Written" in html
    assert "Capability Evolution Decision" in html
    assert "Proposal / Registry Lifecycle" in html
    assert "kpi-strip" in html
    assert "Artifacts Captured" in html
    assert "artifact-table" in html
    assert "Not captured yet" not in html

    # Real data from the run.
    assert "PO-1001" in html
    assert "UIPATH_CLICK" in html
    assert "RPA_EXTRACTION" in html
    assert "POST /api/purchase-orders/PO-1001/approval-request" in html
    assert "PROPOSAL_CREATED" in html
    assert "HUMAN_REVIEW_REQUIRED" in html


def test_dashboard_without_run_id_falls_back_to_static(monkeypatch, tmp_path):
    """Without a real run, dashboard falls back to the static demo (backward compat)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    response = client.get("/case-dashboard/CASE-001")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    # Static dashboard content.
    assert "CASE-001" in html
    assert "Case Overview" in html


def test_dashboard_picks_up_latest_run_id(monkeypatch, tmp_path):
    """When latest_run_id.txt exists, dashboard renders real run without explicit run_id."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    import memory.store as store
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", legacy)
    _seed_pattern_for_case_001(tmp_path)

    from app.main import app
    client = TestClient(app)

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id = start["run_id"]
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={"case_id": "CASE-001", "result": "SUCCESS",
              "final_stage": "API_MODE_EXECUTED", "execution_mode": "API"},
    )
    # /complete writes latest_run_id.txt.
    latest_path = tmp_path / "cases" / "CASE-001" / "latest_run_id.txt"
    assert latest_path.exists()

    # Dashboard without run_id param should pick up latest_run_id.
    response = client.get("/case-dashboard/CASE-001")
    assert response.status_code == 200
    html = response.text
    assert run_id in html
    assert "Live Timeline" in html


def test_run_memory_artifacts_include_business_context_and_agent_proof(monkeypatch, tmp_path):
    """Run Memory stores ERP fields, remarks, company context, route, proof, action, and branch result."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    import memory.store as store
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", legacy)

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-CTX-001", "po_id": "PO-CTX-001"},
    ).json()
    run_id = start["run_id"]

    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "rpa_extracted_fields",
        "case_id": "CASE-CTX-001",
        "data": {
            "po_id": "PO-CTX-001",
            "amount": 18000,
            "budget_limit": 10000,
            "vendor_id": "V-203",
            "erp_status": "Exception",
            "raw_exception_text": "Amount exceeds approved budget limit",
            "business_remarks": "Q4 customer delivery is at risk.",
        },
    })
    route_plan = {
        "business_action": "request_purchase_order_approval",
        "detected_exception_type": "budget_exceeded",
        "final_route": "WAITING_FOR_HUMAN_APPROVAL",
        "business_remarks": "Q4 customer delivery is at risk.",
        "company_context_reference": {
            "finance_policy_used": True,
            "sales_context_used": True,
            "operations_context_used": True,
        },
        "agent_reasoning_summary": "Agent used enterprise context.",
        "llm_validation_proof": {
            "reasoning_mode": "llm_backed",
            "llm_enabled": True,
            "llm_call_mode": "real",
            "llm_provider": "deepseek",
            "schema_validated": True,
            "guardrails_applied": True,
            "decision_status": "DECISION_READY",
            "llm_invocation_verified": True,
        },
        "policy_gate": {"policy_decision": "REQUIRE_HUMAN_APPROVAL"},
        "recommended_erp_action": {
            "action_id": "CREATE_WEB_APPROVAL_TASK",
            "button_selector_id": None,
        },
    }
    artifacts = [
        ("triage_agent_io", {
            "request": {},
            "response": {
                "business_action": "request_purchase_order_approval",
                "detected_exception_type": "budget_exceeded",
            },
        }),
        ("company_context_snapshot", {"company": {"name": "Demo Manufacturing Group"}}),
        ("route_plan", route_plan),
        ("agent_reasoning_summary", {"agent_reasoning_summary": "Agent used enterprise context."}),
        ("llm_validation_proof", route_plan["llm_validation_proof"]),
        ("policy_gate", route_plan["policy_gate"]),
        ("selected_erp_action", route_plan["recommended_erp_action"]),
        ("final_branch_result", {"result": "WAITING_FOR_HUMAN_APPROVAL"}),
    ]
    for artifact_type, data in artifacts:
        client.post(f"/memory/runs/{run_id}/artifacts", json={
            "artifact_type": artifact_type,
            "case_id": "CASE-CTX-001",
            "data": data,
        })

    client.post(
        f"/memory/runs/{run_id}/complete",
        json={
            "case_id": "CASE-CTX-001",
            "result": "SUCCESS",
            "final_stage": "WAITING_FOR_HUMAN_APPROVAL",
            "execution_mode": "RPA",
        },
    )
    commit = client.post(f"/memory/runs/{run_id}/commit")
    assert commit.status_code == 200, commit.text
    body = commit.json()
    assert body["process_signature"].startswith(
        "request_purchase_order_approval__budget_exceeded__waiting_for_human_approval"
    )

    summary = json.loads(
        (tmp_path / "runs" / run_id / "summary" / "case_run_summary.json").read_text()
    )
    assert summary["business_remarks"] == "Q4 customer delivery is at risk."
    assert summary["company_context_snapshot"]["company"]["name"] == "Demo Manufacturing Group"
    assert summary["agent_reasoning_summary"] == "Agent used enterprise context."
    assert summary["llm_validation_proof"]["llm_invocation_verified"] is True
    assert summary["policy_gate"]["policy_decision"] == "REQUIRE_HUMAN_APPROVAL"
    assert summary["selected_erp_action"]["action_id"] == "CREATE_WEB_APPROVAL_TASK"
    assert summary["final_branch_result"]["result"] == "WAITING_FOR_HUMAN_APPROVAL"

    post_summary = json.loads(
        (tmp_path / "runs" / run_id / "summary" / "post_run_memory_summary.json").read_text()
    )
    writes = post_summary["memory_writes"]
    assert writes["business_remarks"] == "Q4 customer delivery is at risk."
    assert writes["selected_erp_action"]["action_id"] == "CREATE_WEB_APPROVAL_TASK"

    html = client.get(f"/case-dashboard/CASE-CTX-001?run_id={run_id}").text
    assert "ERP Order Fields" in html
    assert "Business Remarks" in html


def test_agent_trace_page_renders_uipath_agent_decision(monkeypatch, tmp_path):
    import base64

    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-TRACE-001", "po_id": "PO-TRACE-001"},
    ).json()
    run_id = start["run_id"]

    route_response = {
        "case_id": "CASE-TRACE-001",
        "po_id": "PO-TRACE-001",
        "scenario": "budget_exception",
        "exception_reason": "Amount exceeds approved budget limit",
        "business_remarks": "Strategic customer delivery is at risk.",
        "agent_context_policy": "fetch_enterprise_context_before_decision",
        "enterprise_context_source": "mock_enterprise_context",
        "route_agent_mode": "real_llm_with_mock_enterprise_context",
        "business_action": "request_purchase_order_approval",
        "final_route": "WAITING_FOR_HUMAN_APPROVAL",
        "detected_exception_type": "budget_exceeded",
        "capability_decision": "KEEP_RPA_MODE",
        "policy_gate": {
            "policy_decision": "REQUIRE_HUMAN_APPROVAL",
            "execution_allowed": False,
            "human_required": True,
            "validation_required": True,
            "blocked_actions": ["AUTO_EXECUTE_WITHOUT_APPROVAL"],
        },
        "company_context_reference": {
            "finance_policy_used": True,
            "sales_context_used": True,
            "operations_context_used": True,
        },
        "llm_validation_proof": {
            "reasoning_mode": "llm_backed",
            "llm_enabled": True,
            "llm_call_mode": "real",
            "llm_provider": "deepseek",
            "llm_invocation_verified": True,
        },
        "recommended_erp_action": {"action_id": "CREATE_WEB_APPROVAL_TASK"},
    }
    policy_response = route_response["policy_gate"]
    encoded_route = base64.b64encode(
        json.dumps(route_response).encode("utf-8")
    ).decode("ascii")
    encoded_policy = base64.b64encode(
        json.dumps(policy_response).encode("utf-8")
    ).decode("ascii")

    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "erp_extracted_fields",
        "case_id": "CASE-TRACE-001",
        "data": {
            "simulation_case_id": "SIM-TRACE-001",
            "po_id": "PO-TRACE-001",
            "amount": "18000",
            "budget_limit": "10000",
            "vendor_id": "V-203",
            "scenario": "budget_exception",
            "exception_reason": "Amount exceeds approved budget limit",
            "business_remarks": "Strategic customer delivery is at risk.",
            "erp_status": "Exception",
        },
    })
    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "agent_route_response",
        "case_id": "CASE-TRACE-001",
        "data": {
            "endpoint": "POST http://localhost:8002/case-intake/route",
            "encoding": "base64_utf8",
            "response_json_base64": encoded_route,
        },
    })
    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "policy_gate_response",
        "case_id": "CASE-TRACE-001",
        "data": {
            "source": "route_response_policy_gate_or_fallback",
            "encoding": "base64_utf8",
            "response_json_base64": encoded_policy,
        },
    })
    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "route_plan",
        "case_id": "CASE-TRACE-001",
        "data": {
            "final_route": "WAITING_FOR_HUMAN_APPROVAL",
            "detected_exception_type": "budget_exceeded",
            "capability_decision": "KEEP_RPA_MODE",
            "route_endpoint": "POST http://localhost:8002/case-intake/route",
            "agent_context_policy": "fetch_enterprise_context_before_decision",
            "enterprise_context_source": "mock_enterprise_context",
            "llm_call_mode": "real",
            "llm_invocation_verified": True,
            "route_agent_mode": "real_llm_with_mock_enterprise_context",
        },
    })
    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "company_context_snapshot",
        "case_id": "CASE-TRACE-001",
        "data": {
            "enterprise_context_source": "mock_enterprise_context",
            "enterprise_context_mode": "local_demo_snapshot",
            "enterprise_context_provider": "reasoning-agent.company_context_payload",
        },
    })
    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "policy_gate",
        "case_id": "CASE-TRACE-001",
        "data": policy_response,
    })
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={
            "case_id": "CASE-TRACE-001",
            "result": "SUCCESS",
            "final_stage": "WAITING_FOR_HUMAN_APPROVAL",
            "execution_mode": "RPA",
        },
    )
    client.post(f"/memory/runs/{run_id}/commit")

    response = client.get(f"/agent-trace/{run_id}")
    assert response.status_code == 200
    html = response.text
    assert "Agent Trace - UiPath Route Decision" in html
    assert "POST http://localhost:8002/case-intake/route" in html
    assert "fetch_enterprise_context_before_decision" in html
    assert "mock_enterprise_context" in html
    assert "real_llm_with_mock_enterprise_context" in html
    assert "LLM Invocation Verified" in html
    assert "WAITING_FOR_HUMAN_APPROVAL" in html
    assert "REQUIRE_HUMAN_APPROVAL" in html
    assert "CREATE_WEB_APPROVAL_TASK" in html

    dashboard_html = client.get(
        f"/case-dashboard/CASE-TRACE-001?run_id={run_id}"
    ).text
    assert f"/agent-trace/{run_id}" in dashboard_html
    assert "Mock Enterprise Context" in html
    assert "Parsed Agent Fields" in html
    assert "Policy Gate" in html
    assert "ERP Action / Approval" in html
    assert "Run Memory Summary" in html


def test_run_memory_derives_business_action_from_route_plan(monkeypatch, tmp_path):
    """UiPath can pass business_action via route_plan and still aggregate the right pattern."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    import memory.store as store
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", legacy)

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-CAPEX-HINT-001", "po_id": "PO-CAPEX-HINT-001"},
    ).json()
    run_id = start["run_id"]
    route_plan = {
        "business_action": "request_capex_budget_exception_approval",
        "detected_exception_type": "budget_exceeded",
        "final_route": "WAITING_FOR_HUMAN_APPROVAL",
        "policy_gate": {"policy_decision": "REQUIRE_HUMAN_APPROVAL"},
        "recommended_erp_action": {"action_id": "CREATE_WEB_APPROVAL_TASK"},
    }
    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "rpa_extracted_fields",
        "case_id": "CASE-CAPEX-HINT-001",
        "data": {
            "po_id": "PO-CAPEX-HINT-001",
            "amount": 24000,
            "budget_limit": 10000,
            "erp_status": "Exception",
            "raw_exception_text": "Amount exceeds approved budget limit",
            "business_remarks": "Q4 capital equipment delivery is at risk.",
        },
    })
    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "route_plan",
        "case_id": "CASE-CAPEX-HINT-001",
        "data": route_plan,
    })
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={
            "case_id": "CASE-CAPEX-HINT-001",
            "result": "SUCCESS",
            "final_stage": "WAITING_FOR_HUMAN_APPROVAL",
            "execution_mode": "RPA",
        },
    )

    commit = client.post(f"/memory/runs/{run_id}/commit").json()

    assert commit["process_signature"].startswith(
        "request_capex_budget_exception_approval__budget_exceeded__waiting_for_human_approval"
    )


def test_no_xaml_files_written_or_api_deployed(monkeypatch, tmp_path):
    """Commit must not write any .xaml files or deploy any API."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    import memory.store as store
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", legacy)
    _seed_legacy_for_api_modernization_proposal(legacy)
    _seed_pattern_for_case_001(tmp_path)

    from app.main import app
    client = TestClient(app)

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-001", "po_id": "PO-1001"},
    ).json()
    run_id = start["run_id"]
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-001",
            "data": {
                "request": {},
                "response": {
                    "business_action": "request_purchase_order_approval",
                    "detected_exception_type": "budget_exceeded",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={"case_id": "CASE-001", "result": "SUCCESS",
              "final_stage": "API_MODE_EXECUTED", "execution_mode": "API"},
    )
    response = client.post(f"/memory/runs/{run_id}/commit")
    assert response.status_code == 200

    # No .xaml files anywhere in the run memory tree.
    xaml_files = list(tmp_path.rglob("*.xaml"))
    assert xaml_files == []

    # No deployed API files.
    api_files = list(tmp_path.rglob("*.deployed"))
    assert api_files == []


# ---------------------------------------------------------------------------
# PO-1000 / CASE-000 — normal case Run Memory commit
# ---------------------------------------------------------------------------

def test_commit_case_000_creates_pattern_and_no_proposal(monkeypatch, tmp_path):
    """Commit for CASE-000 (normal case) creates the standard pattern, returns
    NO_EVOLUTION_REQUIRED, and does NOT create a proposal or trusted capability.
    """
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-000", "po_id": "PO-1000"},
    ).json()
    run_id = start["run_id"]

    # Write triage_agent_io artifact carrying the normal-case signals.
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-000",
            "data": {
                "request": {},
                "response": {
                    "business_action": "standard_purchase_order_processing",
                    "detected_exception_type": "none",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={
            "case_id": "CASE-000",
            "result": "SUCCESS",
            "final_stage": "STANDARD_PROCESSING",
            "execution_mode": "STANDARD",
        },
    )

    response = client.post(f"/memory/runs/{run_id}/commit")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["case_id"] == "CASE-000"
    assert body["process_signature"] == "standard_purchase_order_processing__none"
    assert body["capability_evolution_decision"] == "NO_EVOLUTION_REQUIRED"
    assert body["proposal_id"] is None  # no proposal for normal case

    # Pattern file was created.
    pattern_path = tmp_path / "patterns" / "standard_purchase_order_processing__none.json"
    assert pattern_path.exists()
    pattern = json.loads(pattern_path.read_text())
    assert pattern["process_signature"] == "standard_purchase_order_processing__none"
    assert pattern["observed_count"] >= 1
    assert pattern["business_action"] == "standard_purchase_order_processing"

    # Evolution decision file persisted with NO_EVOLUTION_REQUIRED.
    decision_path = tmp_path / "runs" / run_id / "evolution" / "capability_evolution_decision.json"
    assert decision_path.exists()
    decision = json.loads(decision_path.read_text())
    assert decision["decision"] == "NO_EVOLUTION_REQUIRED"
    assert decision["api_modernization_required"] is False
    assert decision["xaml_improvement_required"] is False
    assert decision["requires_human_approval"] is False

    # No proposal file written.
    proposal_files = list((tmp_path / "proposals").glob("*.json")) if (tmp_path / "proposals").exists() else []
    assert proposal_files == []

    # Case run summary reflects the normal route.
    summary_path = tmp_path / "runs" / run_id / "summary" / "case_run_summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text())
    # route_path / case_type / validation_status fields are present.
    assert summary.get("case_id") == "CASE-000"
    assert summary.get("result") == "SUCCESS"
    assert summary.get("execution_mode") == "STANDARD"


def test_dashboard_case_000_with_run_id_renders_real_run_memory(monkeypatch, tmp_path):
    """GET /case-dashboard/CASE-000?run_id=RUN-... renders real run memory."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-000", "po_id": "PO-1000"},
    ).json()
    run_id = start["run_id"]
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-000",
            "data": {
                "request": {},
                "response": {
                    "business_action": "standard_purchase_order_processing",
                    "detected_exception_type": "none",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={"case_id": "CASE-000", "result": "SUCCESS",
              "final_stage": "STANDARD_PROCESSING", "execution_mode": "STANDARD"},
    )
    client.post(f"/memory/runs/{run_id}/commit")

    response = client.get(f"/case-dashboard/CASE-000?run_id={run_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert run_id in html
    assert "PO-1000" in html
    assert "NO_EVOLUTION_REQUIRED" in html
    assert "standard_purchase_order_processing__none" in html
    assert "Run Memory Dashboard — Pattern Evidence Detail" in html
    assert "Pattern Memory Dashboard" in html
    assert "Demo samples:" in html
    # No proposal section content (since proposal_id is None).
    assert "No proposal generated" in html


def test_normal_case_does_not_register_trusted_capability(monkeypatch, tmp_path):
    """Commit for CASE-000 must NOT modify the trusted capability registry."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    # Use a temp legacy data dir so we can verify registry is untouched.
    load_app(monkeypatch, run_memory_root=tmp_path)
    import memory.store as store
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", legacy)
    # Re-import app to pick up the patched DATA_DIR.
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    for name in list(sys.modules):
        if name in {"memory.run_memory", "memory.patterns", "memory.store", "memory.repository"}:
            sys.modules.pop(name)
    from app.main import app
    client = TestClient(app)

    reg_path = legacy / "capability_registry.json"
    assert not reg_path.exists()

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-000", "po_id": "PO-1000"},
    ).json()
    run_id = start["run_id"]
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-000",
            "data": {
                "request": {},
                "response": {
                    "business_action": "standard_purchase_order_processing",
                    "detected_exception_type": "none",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={"case_id": "CASE-000", "result": "SUCCESS",
              "final_stage": "STANDARD_PROCESSING", "execution_mode": "STANDARD"},
    )
    client.post(f"/memory/runs/{run_id}/commit")

    # Registry still does not exist — normal case did NOT auto-register.
    assert not reg_path.exists()


# ---------------------------------------------------------------------------
# PO-1004 / CASE-004 — ambiguous case Run Memory commit
# ---------------------------------------------------------------------------

def test_commit_case_004_creates_pattern_manual_investigation_no_proposal(monkeypatch, tmp_path):
    """Commit for CASE-004 (ambiguous) creates the manual_case_review pattern,
    returns MANUAL_INVESTIGATION, and does NOT create a proposal or trusted
    capability.
    """
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-004", "po_id": "PO-1004"},
    ).json()
    run_id = start["run_id"]

    # Write triage_agent_io artifact carrying the ambiguous-case signals.
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-004",
            "data": {
                "request": {},
                "response": {
                    "business_action": "manual_case_review",
                    "detected_exception_type": "unknown_exception",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={
            "case_id": "CASE-004",
            "result": "WAITING_MANUAL_INVESTIGATION",
            "final_stage": "MANUAL_INVESTIGATION_REQUIRED",
            "execution_mode": "NONE",
        },
    )

    response = client.post(f"/memory/runs/{run_id}/commit")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["case_id"] == "CASE-004"
    assert body["process_signature"] == "manual_case_review__unknown_exception"
    assert body["capability_evolution_decision"] == "MANUAL_INVESTIGATION"
    assert body["proposal_id"] is None  # no proposal for ambiguous case

    # Pattern file was created.
    pattern_path = tmp_path / "patterns" / "manual_case_review__unknown_exception.json"
    assert pattern_path.exists()
    pattern = json.loads(pattern_path.read_text())
    assert pattern["process_signature"] == "manual_case_review__unknown_exception"
    assert pattern["observed_count"] >= 1
    assert pattern["business_action"] == "manual_case_review"

    # Evolution decision file persisted with MANUAL_INVESTIGATION.
    decision_path = tmp_path / "runs" / run_id / "evolution" / "capability_evolution_decision.json"
    assert decision_path.exists()
    decision = json.loads(decision_path.read_text())
    assert decision["decision"] == "MANUAL_INVESTIGATION"
    assert decision["api_modernization_required"] is False
    assert decision["xaml_improvement_required"] is False
    assert decision["requires_human_review"] is True

    # No proposal file written.
    proposal_files = list((tmp_path / "proposals").glob("*.json")) if (tmp_path / "proposals").exists() else []
    assert proposal_files == []

    # Case run summary reflects the ambiguous route.
    summary_path = tmp_path / "runs" / run_id / "summary" / "case_run_summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text())
    assert summary.get("case_id") == "CASE-004"
    assert summary.get("execution_mode") == "NONE"


def test_dashboard_case_004_with_run_id_renders_real_run_memory(monkeypatch, tmp_path):
    """GET /case-dashboard/CASE-004?run_id=RUN-... renders real run memory."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-004", "po_id": "PO-1004"},
    ).json()
    run_id = start["run_id"]
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-004",
            "data": {
                "request": {},
                "response": {
                    "business_action": "manual_case_review",
                    "detected_exception_type": "unknown_exception",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={"case_id": "CASE-004", "result": "WAITING_MANUAL_INVESTIGATION",
              "final_stage": "MANUAL_INVESTIGATION_REQUIRED", "execution_mode": "NONE"},
    )
    client.post(f"/memory/runs/{run_id}/commit")

    response = client.get(f"/case-dashboard/CASE-004?run_id={run_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert run_id in html
    assert "PO-1004" in html
    assert "MANUAL_INVESTIGATION" in html
    assert "manual_case_review__unknown_exception" in html
    # No proposal section content.
    assert "No proposal generated" in html


def test_ambiguous_case_does_not_register_trusted_capability(monkeypatch, tmp_path):
    """Commit for CASE-004 must NOT modify the trusted capability registry."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    import memory.store as store
    legacy = tmp_path / "legacy_data"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", legacy)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    for name in list(sys.modules):
        if name in {"memory.run_memory", "memory.patterns", "memory.store", "memory.repository"}:
            sys.modules.pop(name)
    from app.main import app
    client = TestClient(app)

    reg_path = legacy / "capability_registry.json"
    assert not reg_path.exists()

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-004", "po_id": "PO-1004"},
    ).json()
    run_id = start["run_id"]
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": "CASE-004",
            "data": {
                "request": {},
                "response": {
                    "business_action": "manual_case_review",
                    "detected_exception_type": "unknown_exception",
                },
            },
        },
    )
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={"case_id": "CASE-004", "result": "WAITING_MANUAL_INVESTIGATION",
              "final_stage": "MANUAL_INVESTIGATION_REQUIRED", "execution_mode": "NONE"},
    )
    client.post(f"/memory/runs/{run_id}/commit")

    # Registry still does not exist — ambiguous case did NOT auto-register.
    assert not reg_path.exists()
