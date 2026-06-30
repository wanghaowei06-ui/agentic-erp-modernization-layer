"""Tests for the Real Simulation Loop + Memory-driven Proposal Inbox.

Covers:
  - Simulation queue: reset / enqueue / next / state
  - Threshold-based capability evolution (KEEP_ACCUMULATING_EVIDENCE below threshold)
  - Real-run pattern reaching threshold -> API_MODERNIZATION_PROPOSAL / XAML_WORKFLOW_PROPOSAL
  - Proposal Inbox: only real proposals, approve-for-codex returns prompt but does not call Codex
  - Dashboard source markers: real vs static_fallback
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def load_app(monkeypatch, *, run_memory_root: Path | None = None):
    """Load the FastAPI app with an isolated run memory root."""
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
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
    monkeypatch.delenv("AUTOMATION_MEMORY_DIR", raising=False)

    if str(SERVICE_ROOT) not in sys.path:
        sys.path.insert(0, str(SERVICE_ROOT))
    from app.main import app

    return app


def _clean_data_dir(monkeypatch, tmp_path: Path) -> Path:
    """Redirect memory.store.DATA_DIR to an empty dir (no seed patterns).

    This ensures increment_pattern starts from observed_count=0 instead of
    seeding from historical_patterns.json.
    """
    import memory.store as store

    clean = tmp_path / "clean_data"
    clean.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "DATA_DIR", clean)
    return clean


def _start_and_commit_run(
    client: TestClient,
    *,
    case_id: str,
    po_id: str,
    business_action: str,
    exception_type: str,
    result: str = "SUCCESS",
    execution_mode: str = "RPA",
) -> dict:
    """Start a run, post triage artifact, complete, and commit.

    Returns the commit response JSON.
    """
    start = client.post(
        "/memory/runs/start",
        json={"case_id": case_id, "po_id": po_id},
    ).json()
    run_id = start["run_id"]

    # Post triage_agent_io so derive_business_action / derive_exception_type work.
    client.post(
        f"/memory/runs/{run_id}/artifacts",
        json={
            "artifact_type": "triage_agent_io",
            "case_id": case_id,
            "data": {
                "request": {"po_id": po_id},
                "response": {
                    "business_action": business_action,
                    "detected_exception_type": exception_type,
                },
            },
        },
    )

    # Complete the run.
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={
            "case_id": case_id,
            "result": result,
            "final_stage": "RPA_COMPLETED",
            "execution_mode": execution_mode,
        },
    )

    # Commit -> increments pattern + evaluates capability evolution.
    commit_resp = client.post(f"/memory/runs/{run_id}/commit")
    assert commit_resp.status_code == 200
    return commit_resp.json()


# ---------------------------------------------------------------------------
# Simulation Queue: reset / enqueue / next / state
# ---------------------------------------------------------------------------

def test_simulation_reset_creates_10_default_cases(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    resp = client.post("/simulation/reset")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 10
    assert body["pending"] == 10
    assert body["in_progress"] == 0
    assert body["completed"] == 0
    assert body["reset_at"] is not None

    # Verify the PO IDs match the spec.
    po_ids = [c["po_id"] for c in body["cases"]]
    assert po_ids == [
        "PO-SIM-001", "PO-SIM-002", "PO-SIM-003", "PO-SIM-004", "PO-SIM-005",
        "PO-SIM-006", "PO-SIM-007", "PO-SIM-008", "PO-SIM-009", "PO-SIM-010",
    ]

    # Verify case types: normal majority, exception minority.
    types = [c["case_type"] for c in body["cases"]]
    assert types.count("normal") == 4  # 001, 002, 004, 007
    assert types.count("exception") == 5  # 003, 005, 006, 008, 009
    assert types.count("ambiguous") == 1  # 010


def test_simulation_next_returns_pending_case_and_marks_in_progress(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/simulation/cases/next")
    assert resp.status_code == 200
    case = resp.json()
    assert case["status"] == "in_progress"
    assert case["case_id"] == "CASE-SIM-001"
    assert case["started_at"] is not None

    # State should reflect 1 in_progress.
    state = client.get("/simulation/state").json()
    assert state["pending"] == 9
    assert state["in_progress"] == 1


def test_simulation_next_returns_empty_when_no_pending(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # Drain all 10 cases.
    for _ in range(10):
        resp = client.get("/simulation/cases/next")
        assert resp.status_code == 200
        assert resp.json()["has_case"] is True

    # 11th call should return has_case=false (not 404).
    resp = client.get("/simulation/cases/next")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_case"] is False
    assert body["queue_empty"] is True
    assert "message" in body


def test_simulation_enqueue_adds_custom_case(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/enqueue", json={
        "po_id": "PO-CUSTOM-001",
        "case_type": "exception",
        "amount": 99999,
        "raw_exception_text": "Custom test case",
    })
    assert resp.status_code == 200
    case = resp.json()
    assert case["po_id"] == "PO-CUSTOM-001"
    assert case["status"] == "pending"

    state = client.get("/simulation/state").json()
    assert state["total"] == 11


def test_simulation_state_reflects_queue(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/simulation/cases/next")

    state = client.get("/simulation/state").json()
    assert state["total"] == 10
    assert state["pending"] == 9
    assert state["in_progress"] == 1
    assert state["completed"] == 0


# ---------------------------------------------------------------------------
# Threshold-based capability evolution
# ---------------------------------------------------------------------------

def test_two_budget_exceeded_runs_return_keep_accumulating_evidence(monkeypatch, tmp_path):
    """Two commits of budget_exceeded should NOT generate a proposal."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    # Re-load app so memory.store.DATA_DIR patch is picked up by patterns module.
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    # 1st budget_exceeded run.
    commit1 = _start_and_commit_run(
        client,
        case_id="CASE-SIM-003",
        po_id="PO-SIM-003",
        business_action="request_purchase_order_approval",
        exception_type="budget_exceeded",
    )
    assert commit1["capability_evolution_decision"] == "KEEP_ACCUMULATING_EVIDENCE"
    assert commit1["proposal_id"] is None

    # 2nd budget_exceeded run.
    commit2 = _start_and_commit_run(
        client,
        case_id="CASE-SIM-006",
        po_id="PO-SIM-006",
        business_action="request_purchase_order_approval",
        exception_type="budget_exceeded",
    )
    assert commit2["capability_evolution_decision"] == "KEEP_ACCUMULATING_EVIDENCE"
    assert commit2["proposal_id"] is None


def test_third_budget_exceeded_run_generates_api_modernization_proposal(monkeypatch, tmp_path):
    """Third commit of budget_exceeded should generate API_MODERNIZATION_PROPOSAL."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    # Run 3 budget_exceeded commits.
    for i, po_id in enumerate(["PO-SIM-003", "PO-SIM-006", "PO-SIM-008"], start=1):
        commit = _start_and_commit_run(
            client,
            case_id=f"CASE-SIM-{i:03d}",
            po_id=po_id,
            business_action="request_purchase_order_approval",
            exception_type="budget_exceeded",
        )
        if i < 3:
            assert commit["capability_evolution_decision"] == "KEEP_ACCUMULATING_EVIDENCE"
            assert commit["proposal_id"] is None
        else:
            assert commit["capability_evolution_decision"] == "API_MODERNIZATION_PROPOSAL"
            assert commit["proposal_id"] is not None
            assert commit["proposal_id"].startswith("PROP-API-")


def test_capex_budget_seed_reaches_api_proposal_after_three_real_commits(monkeypatch, tmp_path):
    """CAPEX demo uses an untrusted business_action and reaches API proposal naturally."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    for i in range(1, 4):
        commit = _start_and_commit_run(
            client,
            case_id=f"CASE-CAPEX-{i:03d}",
            po_id=f"PO-CAPEX-{i:03d}",
            business_action="request_capex_budget_exception_approval",
            exception_type="budget_exceeded",
        )
        if i < 3:
            assert commit["capability_evolution_decision"] == "KEEP_ACCUMULATING_EVIDENCE"
            assert commit["proposal_id"] is None
        else:
            assert commit["capability_evolution_decision"] == "API_MODERNIZATION_PROPOSAL"
            assert commit["proposal_id"].startswith("PROP-API-")

    html = client.get("/simulation/dashboard").text
    assert "request_capex_budget_exception_approval" in html
    assert "3/3" in html
    assert "API_MODERNIZATION_PROPOSAL" in html


def test_third_inventory_shortage_generates_xaml_workflow_proposal(monkeypatch, tmp_path):
    """Third commit of inventory_shortage should generate XAML_WORKFLOW_PROPOSAL."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    for i in range(1, 4):
        commit = _start_and_commit_run(
            client,
            case_id=f"CASE-SIM-INV-{i:03d}",
            po_id=f"PO-SIM-INV-{i:03d}",
            business_action="request_inventory_review",
            exception_type="inventory_shortage",
        )
        if i < 3:
            assert commit["capability_evolution_decision"] == "KEEP_ACCUMULATING_EVIDENCE"
        else:
            assert commit["capability_evolution_decision"] == "XAML_WORKFLOW_PROPOSAL"
            assert commit["proposal_id"] is not None
            assert commit["proposal_id"].startswith("PROP-XAMLW-")


# ---------------------------------------------------------------------------
# Proposal Inbox
# ---------------------------------------------------------------------------

def test_proposal_inbox_only_shows_real_proposals(monkeypatch, tmp_path):
    """Proposal inbox should only list proposals from memory/proposals/."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    # Initially no proposals.
    inbox = client.get("/proposals/inbox?format=json").json()
    assert inbox["total"] == 0

    # Generate 1 proposal via 3 budget_exceeded commits.
    for i in range(1, 4):
        _start_and_commit_run(
            client,
            case_id=f"CASE-SIM-PB-{i:03d}",
            po_id=f"PO-SIM-PB-{i:03d}",
            business_action="request_purchase_order_approval",
            exception_type="budget_exceeded",
        )

    inbox = client.get("/proposals/inbox?format=json").json()
    assert inbox["total"] == 1
    prop = inbox["proposals"][0]
    assert prop["proposal_type"] == "API_MODERNIZATION_PROPOSAL"
    assert prop["status"] == "PROPOSAL_CREATED"
    assert prop["auto_execution_allowed"] is False
    assert prop["observed_count"] == 3
    assert prop["threshold"] == 3
    assert prop["source_pattern"] == "request_purchase_order_approval__budget_exceeded"
    assert prop["latest_run_id"]
    assert prop["source_run_ids"]
    assert prop["recommended_change"]
    assert prop["human_review_required"] is True
    assert prop["coding_agent_allowed"] == "after_approval_only"


def test_get_proposal_by_id_returns_full_proposal(monkeypatch, tmp_path):
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    for i in range(1, 4):
        commit = _start_and_commit_run(
            client,
            case_id=f"CASE-SIM-GP-{i:03d}",
            po_id=f"PO-SIM-GP-{i:03d}",
            business_action="request_purchase_order_approval",
            exception_type="budget_exceeded",
        )

    proposal_id = commit["proposal_id"]
    resp = client.get(f"/proposals/{proposal_id}")
    assert resp.status_code == 200
    proposal = resp.json()
    assert proposal["proposal_id"] == proposal_id
    assert proposal["proposal_type"] == "API_MODERNIZATION_PROPOSAL"
    assert proposal["process_signature"] == "request_purchase_order_approval__budget_exceeded"
    assert proposal["codex_prompt"]
    assert proposal["auto_execution_allowed"] is False
    assert proposal["validation_required"] is True
    assert "risks" in proposal
    assert "threshold" in proposal


def test_approve_for_codex_returns_prompt_but_does_not_call_codex(monkeypatch, tmp_path):
    """approve-for-codex changes status and returns codex_prompt.

    It must NOT call Codex, NOT modify XAML, NOT deploy API.
    """
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    for i in range(1, 4):
        commit = _start_and_commit_run(
            client,
            case_id=f"CASE-SIM-AC-{i:03d}",
            po_id=f"PO-SIM-AC-{i:03d}",
            business_action="request_purchase_order_approval",
            exception_type="budget_exceeded",
        )

    proposal_id = commit["proposal_id"]

    # Before approval.
    before = client.get(f"/proposals/{proposal_id}").json()
    assert before["status"] == "PROPOSAL_CREATED"

    # Approve.
    resp = client.post(f"/proposals/{proposal_id}/approve-for-codex")
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposal_id"] == proposal_id
    assert body["previous_status"] == "PROPOSAL_CREATED"
    assert body["new_status"] == "APPROVED_FOR_CODEX_PROMPT"
    assert body["codex_prompt"]
    # Safety: Codex was NOT called, no XAML modified, no API deployed.
    assert body["codex_called"] is False
    assert body["xaml_modified"] is False
    assert body["api_deployed"] is False
    assert body["auto_execution_allowed"] is False

    # After approval, status is persisted.
    after = client.get(f"/proposals/{proposal_id}").json()
    assert after["status"] == "APPROVED_FOR_CODEX_PROMPT"


def test_proposals_inbox_has_human_codex_handoff_button(monkeypatch, tmp_path):
    """Proposal inbox exposes a visible human approval button for Codex CLI."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    for i in range(1, 4):
        commit = _start_and_commit_run(
            client,
            case_id=f"CASE-SIM-HANDOFF-{i:03d}",
            po_id=f"PO-SIM-HANDOFF-{i:03d}",
            business_action="request_capex_budget_exception_approval",
            exception_type="budget_exceeded",
        )

    html = client.get("/proposals/inbox").text

    assert "Approve and Start Codex CLI" in html
    assert f"/proposals/{commit['proposal_id']}/approve-and-start-codex-form" in html


def test_proposals_inbox_contains_responsive_table_layout(monkeypatch, tmp_path):
    """Proposal inbox keeps long proposal evidence out of the primary table columns."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    for i in range(1, 4):
        _start_and_commit_run(
            client,
            case_id=f"CASE-SIM-PROP-LAYOUT-{i:03d}",
            po_id=f"PO-SIM-PROP-LAYOUT-{i:03d}",
            business_action="request_capex_budget_exception_approval",
            exception_type="budget_exceeded",
        )

    html = client.get("/proposals/inbox").text
    assert "proposal-inbox-page" in html
    assert "proposal-table-wrap" in html
    assert "proposal-detail" in html
    assert "Details</summary>" in html
    assert "<th>Recommended Change</th>" not in html
    assert "<th>Source Pattern</th>" not in html
    assert "<th>Auto Exec</th>" not in html


def test_approve_and_start_codex_creates_visible_session(monkeypatch, tmp_path):
    """Explicit human approval starts a Codex CLI session and exposes logs."""
    monkeypatch.setenv("CODEX_CLI_DEMO_MODE", "mock")
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    for i in range(1, 4):
        commit = _start_and_commit_run(
            client,
            case_id=f"CASE-SIM-CODEX-{i:03d}",
            po_id=f"PO-SIM-CODEX-{i:03d}",
            business_action="request_capex_budget_exception_approval",
            exception_type="budget_exceeded",
        )
    proposal_id = commit["proposal_id"]

    resp = client.post(f"/proposals/{proposal_id}/approve-and-start-codex")

    assert resp.status_code == 200
    body = resp.json()
    assert body["proposal_id"] == proposal_id
    assert body["codex_called"] is True
    assert body["codex_cli_started"] is True
    assert body["api_deployed"] is False
    assert body["xaml_modified"] is False
    assert body["trusted_capability_registered"] is False

    status = client.get(f"{body['codex_session_url']}/status").json()
    assert status["proposal_id"] == proposal_id
    assert status["status"] in {"QUEUED", "RUNNING", "COMPLETED"}
    assert status["execution_mode"] == "mock"
    assert status["mode_label"] == "Demo Mock Stream"
    assert "activity_events" in status
    assert "display_steps" in status
    assert status["api_deployed"] is False
    assert status["xaml_modified"] is False
    assert status["trusted_capability_registered"] is False
    assert status["draft_pr_created"] is False

    html = client.get(body["codex_session_url"]).text
    assert "Codex CLI Handoff Session" in html
    assert "Readable Activity Stream" in html
    assert "Execution Progress" in html
    assert "Raw CLI Output" in html
    assert "Demo Mock Stream" in html
    assert "Draft PR Handoff Ready" in html
    assert "starts only after human proposal approval" in html


def test_approve_for_codex_404_for_unknown_proposal(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.post("/proposals/PROP-FAKE-9999/approve-for-codex")
    assert resp.status_code == 404


def test_codex_prompt_content_for_api_proposal(monkeypatch, tmp_path):
    """The codex_prompt for an API proposal should mention FastAPI and human review."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    for i in range(1, 4):
        commit = _start_and_commit_run(
            client,
            case_id=f"CASE-SIM-CP-{i:03d}",
            po_id=f"PO-SIM-CP-{i:03d}",
            business_action="request_purchase_order_approval",
            exception_type="budget_exceeded",
        )

    proposal = client.get(f"/proposals/{commit['proposal_id']}").json()
    prompt = proposal["codex_prompt"]
    assert "FastAPI" in prompt or "endpoint" in prompt.lower()
    assert "human review" in prompt.lower()


# ---------------------------------------------------------------------------
# Dashboard source markers
# ---------------------------------------------------------------------------

def test_evidence_snapshot_marks_source_static_fallback(monkeypatch, tmp_path):
    """With no real runs, evidence snapshot source should be static_fallback."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    snap = client.get("/demo/evidence-snapshot").json()
    assert snap["source"] == "static_fallback"
    assert snap["source_detail"]["real_run_memory_cases"] == 0
    for case in snap["cases"]:
        assert case["source"] == "static_fallback"


def test_evidence_snapshot_marks_source_real_when_run_exists(monkeypatch, tmp_path):
    """When a real run exists for a case, that case's source should be real_run_memory."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    # Create a real run for CASE-001 (one of the 5 canonical demo cases).
    _start_and_commit_run(
        client,
        case_id="CASE-001",
        po_id="PO-1001",
        business_action="request_purchase_order_approval",
        exception_type="budget_exceeded",
    )

    snap = client.get("/demo/evidence-snapshot").json()
    assert snap["source"] == "real_run_memory"
    case_001 = [c for c in snap["cases"] if c["case_id"] == "CASE-001"][0]
    assert case_001["source"] == "real_run_memory"


def test_case_portfolio_has_source_markers(monkeypatch, tmp_path):
    """The portfolio HTML should contain source markers."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    resp = client.get("/case-portfolio")
    assert resp.status_code == 200
    html = resp.text
    assert "source-static" in html or "static fallback" in html


def test_case_router_lab_has_source_marker(monkeypatch, tmp_path):
    """The router-lab HTML should contain a static_fallback source marker."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    resp = client.get("/case-router-lab")
    assert resp.status_code == 200
    html = resp.text
    assert "static_fallback" in html


def test_simulation_dashboard_returns_html(monkeypatch, tmp_path):
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/simulation/dashboard")
    assert resp.status_code == 200
    html = resp.text
    assert "Simulation Dashboard" in html
    assert "Agentic ERP Modernization — Pattern Memory Dashboard" in html
    assert "Total Real Runs" in html
    assert "Active Process Patterns" in html
    assert "Patterns Accumulating Evidence" in html
    assert "Patterns Reached Threshold" in html
    assert "Open Proposals" in html
    assert "API Candidates" in html
    assert "XAML Workflow Candidates" in html
    assert "What Counts As The Same Process" in html
    assert "process_signature = business_action + exception_type + route_family + policy_gate_family + side_effects_signature" in html
    assert "Pattern Memory Table" in html
    assert "Latest Real Runs" in html
    assert "Proposal Pipeline" in html
    assert "Simulation Queue" in html
    assert "Current work items waiting for UiPath Robot." in html
    assert "Demo Samples / Seed Scenarios" in html


def test_simulation_dashboard_has_demo_ux_consolidation_sections(monkeypatch, tmp_path):
    """Dashboard should be a Pattern Memory overview, not a static 5-case dump."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    html = client.get("/simulation/dashboard").text
    for label in ["Pattern Memory Table", "Latest Real Runs", "Proposal Pipeline", "Demo Samples / Seed Scenarios"]:
        assert label in html
    assert "No real proposals yet. Proposal creation is not button-driven" in html
    assert "No Pattern Memory yet. Inject demo seed cases" in html
    assert "Operational Links" in html
    assert "No Codex calls, no XAML modifications, no API deployments" not in html
    assert "Canonical cases remain available as sample dashboards only" in html
    # Full proposal-inbox detail columns belong on /proposals/inbox, not the overview.
    assert "Auto Exec" not in html
    assert "Evidence Run IDs" not in html


def test_simulation_dashboard_contains_responsive_pattern_layout(monkeypatch, tmp_path):
    """Wide Pattern Memory evidence should be contained inside the dashboard layout."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    _start_and_commit_run(
        client,
        case_id="CASE-SIM-LAYOUT-001",
        po_id="PO-SIM-LAYOUT-001",
        business_action="request_purchase_order_approval",
        exception_type="budget_exceeded",
    )

    html = client.get("/simulation/dashboard").text
    assert "simulation-dashboard" in html
    assert "simulation-table-wrap" in html
    assert "pattern-evidence" in html
    assert "Evidence</summary>" in html
    assert "<th>Business Remarks Examples</th>" not in html
    assert "<th>Company Context Used</th>" not in html
    assert "<th>Agent Analysis Summary</th>" not in html


def test_simulation_dashboard_shows_real_proposals(monkeypatch, tmp_path):
    """The simulation dashboard should show real proposals when they exist."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    # Generate a proposal.
    for i in range(1, 4):
        _start_and_commit_run(
            client,
            case_id=f"CASE-SIM-SD-{i:03d}",
            po_id=f"PO-SIM-SD-{i:03d}",
            business_action="request_purchase_order_approval",
            exception_type="budget_exceeded",
        )

    resp = client.get("/simulation/dashboard")
    assert resp.status_code == 200
    html = resp.text
    assert "Proposal Pipeline" in html
    assert "Open Capability Proposal Inbox" in html
    assert "API_MODERNIZATION_PROPOSAL" in html
    assert "Auto Exec" not in html
    assert "process_signature" in html
    assert "Agent Analysis Summary" in html
    assert "reasoning_mode=deterministic_rule" in html


def test_simulation_dashboard_shows_accumulating_pattern_before_threshold(monkeypatch, tmp_path):
    """Below threshold, dashboard should show KEEP_ACCUMULATING_EVIDENCE."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    commit = _start_and_commit_run(
        client,
        case_id="CASE-SIM-ACC-001",
        po_id="PO-SIM-ACC-001",
        business_action="request_purchase_order_approval",
        exception_type="budget_exceeded",
    )

    html = client.get("/simulation/dashboard").text
    assert "request_purchase_order_approval__budget_exceeded" in html
    assert "KEEP_ACCUMULATING_EVIDENCE" in html
    assert "Still accumulating evidence" in html
    assert f"/case-dashboard/CASE-SIM-ACC-001?run_id={commit['run_id']}" in html


def test_monitoring_live_data_exposes_pattern_threshold_progress(monkeypatch, tmp_path):
    """Monitoring JSON shows observed/threshold progress from real Pattern Memory."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    _start_and_commit_run(
        client,
        case_id="CASE-PROGRESS-001",
        po_id="PO-PROGRESS-001",
        business_action="request_capex_budget_exception_approval",
        exception_type="budget_exceeded",
    )

    data = client.get("/monitoring/live-data").json()

    assert "pattern_progress" in data
    row = data["pattern_progress"][0]
    assert row["business_action"] == "request_capex_budget_exception_approval"
    assert row["observed_count"] == 1
    assert row["threshold"] == 3
    assert row["progress_label"] == "1/3"
    assert row["recommended_next_step"] == "KEEP_ACCUMULATING_EVIDENCE"


def test_simulation_dashboard_shows_business_context_pattern_examples(monkeypatch, tmp_path):
    """Pattern dashboard should surface remarks, company context, and agent reasoning."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    start = client.post(
        "/memory/runs/start",
        json={"case_id": "CASE-SIM-CONTEXT-001", "po_id": "PO-SIM-CONTEXT-001"},
    ).json()
    run_id = start["run_id"]
    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "rpa_extracted_fields",
        "case_id": "CASE-SIM-CONTEXT-001",
        "data": {"business_remarks": "Q4 customer delivery is at risk."},
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
        "agent_reasoning_summary": "Agent chose approval because strategic-account context matters.",
        "policy_gate": {"policy_decision": "REQUIRE_HUMAN_APPROVAL"},
        "recommended_erp_action": {"action_id": "CREATE_WEB_APPROVAL_TASK"},
    }
    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "triage_agent_io",
        "case_id": "CASE-SIM-CONTEXT-001",
        "data": {
            "request": {},
            "response": {
                "business_action": "request_purchase_order_approval",
                "detected_exception_type": "budget_exceeded",
            },
        },
    })
    client.post(f"/memory/runs/{run_id}/artifacts", json={
        "artifact_type": "route_plan",
        "case_id": "CASE-SIM-CONTEXT-001",
        "data": route_plan,
    })
    client.post(
        f"/memory/runs/{run_id}/complete",
        json={
            "case_id": "CASE-SIM-CONTEXT-001",
            "result": "SUCCESS",
            "final_stage": "WAITING_FOR_HUMAN_APPROVAL",
            "execution_mode": "RPA",
        },
    )
    client.post(f"/memory/runs/{run_id}/commit")

    html = client.get("/simulation/dashboard").text
    assert "Business Remarks Examples" in html
    assert "Company Context Used" in html
    assert "Why Agent Chose Route" in html
    assert "Q4 customer delivery is at risk." in html
    assert "finance_policy_used" in html
    assert "Agent chose approval because strategic-account context matters." in html
    assert "KEEP_ACCUMULATING_EVIDENCE" in html


def test_pattern_detail_page_is_read_only_and_links_runs(monkeypatch, tmp_path):
    """Pattern detail should render real Pattern Memory and latest run links."""
    load_app(monkeypatch, run_memory_root=tmp_path)
    _clean_data_dir(monkeypatch, tmp_path)
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _clean_data_dir(monkeypatch, tmp_path)

    commit = _start_and_commit_run(
        client,
        case_id="CASE-SIM-PATTERN-001",
        po_id="PO-SIM-PATTERN-001",
        business_action="request_purchase_order_approval",
        exception_type="budget_exceeded",
    )
    run_id = commit["run_id"]

    resp = client.get("/patterns/request_purchase_order_approval__budget_exceeded")
    assert resp.status_code == 200
    html = resp.text
    assert "Pattern Detail" in html
    assert "Agent Analysis Summary" in html
    assert "KEEP_ACCUMULATING_EVIDENCE" in html
    assert run_id in html
    assert f"/case-dashboard/CASE-SIM-PATTERN-001?run_id={run_id}" in html
    assert "No proposal has been created for this pattern yet." in html


# ---------------------------------------------------------------------------
# Simulation Complete — close the simulation loop
# ---------------------------------------------------------------------------

def test_simulation_complete_marks_case_completed(monkeypatch, tmp_path):
    """After next + complete, pending=9, in_progress=0, completed=1."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # next -> 1 in_progress
    nxt = client.get("/simulation/cases/next").json()
    assert nxt["status"] == "in_progress"
    assert nxt["simulation_case_id"] == "SIM-001"

    # complete -> 1 completed
    resp = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "case_id": nxt["case_id"],
        "po_id": nxt["po_id"],
        "run_id": "RUN-TEST-001",
        "result": "WAITING_APPROVAL",
        "final_route": "WAITING_FOR_HUMAN_APPROVAL",
        "policy_decision": "REQUIRE_HUMAN_APPROVAL",
        "memory_commit": "COMPLETED",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["run_id"] == "RUN-TEST-001"
    assert body["result"] == "WAITING_APPROVAL"
    assert body["final_route"] == "WAITING_FOR_HUMAN_APPROVAL"
    assert body["policy_decision"] == "REQUIRE_HUMAN_APPROVAL"
    assert body["memory_commit"] == "COMPLETED"
    assert body["completed_at"] is not None

    # queue_summary.
    qs = body["queue_summary"]
    assert qs["pending"] == 9
    assert qs["in_progress"] == 0
    assert qs["completed"] == 1
    assert qs["failed"] == 0


def test_simulation_complete_by_case_id(monkeypatch, tmp_path):
    """complete should find case by case_id when simulation_case_id is not given."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    nxt = client.get("/simulation/cases/next").json()

    resp = client.post("/simulation/cases/complete", json={
        "case_id": nxt["case_id"],
        "run_id": "RUN-BY-CASE-ID",
        "result": "SUCCESS",
        "final_route": "API_MODE_EXECUTED",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["run_id"] == "RUN-BY-CASE-ID"


def test_simulation_complete_by_po_id(monkeypatch, tmp_path):
    """complete should find case by po_id."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/simulation/cases/next")

    resp = client.post("/simulation/cases/complete", json={
        "po_id": "PO-SIM-001",
        "run_id": "RUN-BY-PO",
        "result": "SUCCESS",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


def test_simulation_complete_marks_failed_on_error_result(monkeypatch, tmp_path):
    """If result=FAILED, case should be marked as failed."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/simulation/cases/next")

    resp = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "run_id": "RUN-FAIL-001",
        "result": "FAILED",
        "final_route": "MANUAL_INVESTIGATION",
        "memory_commit": "FAILED",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["queue_summary"]["failed"] == 1
    assert body["queue_summary"]["completed"] == 0


@pytest.mark.parametrize("result", [
    "ERP_STANDARD_PROCESSED",
    "ERP_WAITING_VENDOR_INFO",
    "ERP_CAPABILITY_GAP_FLAGGED",
    "ERP_MANUAL_INVESTIGATION_REQUIRED",
    "WAITING_WEB_APPROVAL",
])
def test_simulation_complete_accepts_uipath_business_results(monkeypatch, tmp_path, result):
    """Main.xaml branchResult values are normal business outcomes."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/simulation/cases/next")

    resp = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "run_id": f"RUN-{result}",
        "result": result,
        "final_route": "STANDARD_PROCESSING",
        "policy_decision": "ALLOW_STANDARD_PROCESSING",
        "memory_commit": "COMPLETED",
    })

    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["queue_summary"]["failed"] == 0


def test_simulation_complete_memory_commit_skipped_is_completed(monkeypatch, tmp_path):
    """memory_commit=SKIPPED is not a business failure for the simulation queue."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/simulation/cases/next")

    resp = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "run_id": "RUN-SKIPPED-COMMIT",
        "result": "ERP_STANDARD_PROCESSED",
        "final_route": "STANDARD_PROCESSING",
        "policy_decision": "ALLOW_STANDARD_PROCESSING",
        "memory_commit": "SKIPPED",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["memory_commit"] == "SKIPPED"
    assert body["queue_summary"]["completed"] == 1
    assert body["queue_summary"]["failed"] == 0


def test_simulation_complete_pending_dirty_action_with_valid_run_id(monkeypatch, tmp_path):
    """A pre-fix dirty pending case with last_action and a real run can close."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.post("/erp/work-queue/SIM-001/send-manual-investigation-form",
                follow_redirects=False)
    run = client.post("/memory/runs/start", json={
        "case_id": "CASE-SIM-001",
        "po_id": "PO-SIM-001",
        "workflow_name": "Main.xaml",
        "source": "uipath_rpa_erp_worker",
        "demo_mode": True,
    }).json()

    resp = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "case_id": "CASE-SIM-001",
        "po_id": "PO-SIM-001",
        "run_id": run["run_id"],
        "result": "ERP_MANUAL_INVESTIGATION_REQUIRED",
        "final_route": "MANUAL_INVESTIGATION",
        "policy_decision": "REQUIRE_MANUAL_INVESTIGATION",
        "memory_commit": "SKIPPED",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["started_at"] is not None


def test_simulation_complete_pending_dirty_action_rejects_fake_run_id(monkeypatch, tmp_path):
    """Dirty pending compatibility still requires a real Run Memory run."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.post("/erp/work-queue/SIM-001/send-manual-investigation-form",
                follow_redirects=False)

    resp = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "run_id": "RUN-DOES-NOT-EXIST",
        "result": "ERP_MANUAL_INVESTIGATION_REQUIRED",
        "memory_commit": "SKIPPED",
    })

    assert resp.status_code == 400
    assert "cannot complete" in resp.json()["detail"].lower()


def test_simulation_complete_404_for_unknown_case(monkeypatch, tmp_path):
    """Unknown case should return 404, not 500."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-999",
        "case_id": "CASE-FAKE",
        "po_id": "PO-FAKE",
        "run_id": "RUN-FAKE",
        "result": "SUCCESS",
    })
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_simulation_complete_400_for_pending_case(monkeypatch, tmp_path):
    """Cannot complete a case that is still pending (not in_progress)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-002",
        "run_id": "RUN-TEST",
        "result": "SUCCESS",
    })
    assert resp.status_code == 400
    assert "cannot complete" in resp.json()["detail"].lower()


def test_simulation_state_contains_run_id_and_result_after_complete(monkeypatch, tmp_path):
    """After complete, /simulation/state should show run_id/result/final_route."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/simulation/cases/next")

    client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "run_id": "RUN-STATE-001",
        "result": "WAITING_APPROVAL",
        "final_route": "WAITING_FOR_HUMAN_APPROVAL",
        "policy_decision": "REQUIRE_HUMAN_APPROVAL",
        "memory_commit": "COMPLETED",
    })

    state = client.get("/simulation/state").json()
    case = [c for c in state["cases"] if c["simulation_case_id"] == "SIM-001"][0]
    assert case["status"] == "completed"
    assert case["run_id"] == "RUN-STATE-001"
    assert case["result"] == "WAITING_APPROVAL"
    assert case["final_route"] == "WAITING_FOR_HUMAN_APPROVAL"
    assert case["policy_decision"] == "REQUIRE_HUMAN_APPROVAL"
    assert case["memory_commit"] == "COMPLETED"
    assert case["started_at"] is not None
    assert case["completed_at"] is not None


def test_simulation_dashboard_shows_completed_case_and_link(monkeypatch, tmp_path):
    """Dashboard should include completed case with run_id and dashboard link."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/simulation/cases/next")

    client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "run_id": "RUN-DASH-001",
        "result": "SUCCESS",
        "final_route": "API_MODE_EXECUTED",
        "policy_decision": "ALLOW_STANDARD_PROCESSING",
        "memory_commit": "COMPLETED",
    })

    resp = client.get("/simulation/dashboard")
    assert resp.status_code == 200
    html = resp.text
    # Completed case appears.
    assert "RUN-DASH-001" in html
    assert "completed" in html
    # Dashboard link with run_id.
    assert "/case-dashboard/CASE-SIM-001?run_id=RUN-DASH-001" in html
    # Overview sections remain visible after processing starts.
    assert "Latest Real Runs" in html
    assert "Proposal Pipeline" in html


def test_simulation_dashboard_shows_failed_case(monkeypatch, tmp_path):
    """Dashboard should show failed case with red styling."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/simulation/cases/next")

    client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "run_id": "RUN-FAIL-DASH",
        "result": "FAILED",
        "final_route": "MANUAL_INVESTIGATION",
        "memory_commit": "FAILED",
    })

    resp = client.get("/simulation/dashboard")
    html = resp.text
    assert "failed" in html
    assert "RUN-FAIL-DASH" in html


def test_evidence_snapshot_includes_simulation_summary(monkeypatch, tmp_path):
    """Evidence snapshot should include simulation_summary with counts."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/simulation/cases/next")
    client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "run_id": "RUN-SNAP-001",
        "result": "SUCCESS",
        "final_route": "API_MODE_EXECUTED",
        "memory_commit": "COMPLETED",
    })

    snap = client.get("/demo/evidence-snapshot").json()
    sim = snap["simulation_summary"]
    assert sim["total"] == 10
    assert sim["pending"] == 9
    assert sim["in_progress"] == 0
    assert sim["completed"] == 1
    assert sim["failed"] == 0
    assert len(sim["completed_cases"]) == 1
    assert sim["completed_cases"][0]["run_id"] == "RUN-SNAP-001"
    assert sim["completed_cases"][0]["status"] == "completed"


def test_simulation_full_lifecycle(monkeypatch, tmp_path):
    """Full lifecycle: reset → next → complete for multiple cases."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # Process 3 cases.
    for i in range(3):
        nxt = client.get("/simulation/cases/next").json()
        client.post("/simulation/cases/complete", json={
            "simulation_case_id": nxt["simulation_case_id"],
            "case_id": nxt["case_id"],
            "po_id": nxt["po_id"],
            "run_id": f"RUN-LIFE-{i+1:03d}",
            "result": "SUCCESS",
            "final_route": "API_MODE_EXECUTED",
            "memory_commit": "COMPLETED",
        })

    state = client.get("/simulation/state").json()
    assert state["pending"] == 7
    assert state["in_progress"] == 0
    assert state["completed"] == 3
    assert state["failed"] == 0

    # All 3 completed cases have run_ids.
    completed = [c for c in state["cases"] if c["status"] == "completed"]
    assert len(completed) == 3
    run_ids = {c["run_id"] for c in completed}
    assert run_ids == {"RUN-LIFE-001", "RUN-LIFE-002", "RUN-LIFE-003"}


# ---------------------------------------------------------------------------
# Simulation Inject — scenario-based case injection
# ---------------------------------------------------------------------------

def test_simulation_inject_budget_exceeded(monkeypatch, tmp_path):
    """Inject 2 budget_exceeded cases into the queue."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/inject", json={
        "scenario": "budget_exceeded",
        "count": 2,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["injected_count"] == 2
    assert body["scenario"] == "budget_exceeded"
    assert len(body["cases"]) == 2

    # Verify the cases are in the queue as pending.
    state = client.get("/simulation/state").json()
    assert state["total"] == 12  # 10 default + 2 injected
    assert state["pending"] == 12

    # Verify scenario content.
    for c in body["cases"]:
        assert c["case_type"] == "exception"
        assert c["amount"] == 18000
        assert c["status"] == "pending"
        assert "Amount exceeds approved budget limit" in c["raw_exception_text"]


def test_simulation_inject_normal(monkeypatch, tmp_path):
    """Inject normal scenario cases."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/inject", json={
        "scenario": "normal",
        "count": 3,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["injected_count"] == 3
    for c in body["cases"]:
        assert c["case_type"] == "normal"
        assert c["amount"] == 5000


def test_simulation_inject_ambiguous(monkeypatch, tmp_path):
    """Inject ambiguous scenario case."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/inject", json={
        "scenario": "ambiguous",
        "count": 1,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["injected_count"] == 1
    assert body["cases"][0]["case_type"] == "ambiguous"


def test_simulation_inject_all_scenarios(monkeypatch, tmp_path):
    """All base and demo scenarios should be injectable."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    for scenario in ["normal", "budget_exceeded", "vendor_info_missing",
                     "inventory_shortage", "ambiguous",
                     "agent_context_review", "capex_budget_exception"]:
        resp = client.post("/simulation/inject", json={
            "scenario": scenario,
            "count": 1,
        })
        assert resp.status_code == 200, f"Failed for scenario: {scenario}"


def test_simulation_inject_capex_seed_has_business_context(monkeypatch, tmp_path):
    """CAPEX demo seed carries action hint and context policy but only enters queue."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/inject", json={
        "scenario": "capex_budget_exception",
        "count": 3,
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["injected_count"] == 3
    for case in body["cases"]:
        assert case["business_action"] == "request_capex_budget_exception_approval"
        assert case["agent_context_policy"] == "fetch_enterprise_context_before_decision"
        assert case["demo_purpose"] == "api_modernization_proposal_seed"
        assert case["status"] == "pending"
    inbox = client.get("/proposals/inbox?format=json").json()
    assert inbox["total"] == 0


def test_simulation_inject_unknown_scenario_400(monkeypatch, tmp_path):
    """Unknown scenario should return 400."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.post("/simulation/inject", json={
        "scenario": "bogus_scenario",
        "count": 1,
    })
    assert resp.status_code == 400


def test_simulation_inject_with_full_payload(monkeypatch, tmp_path):
    """Inject a case with full custom payload."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/inject", json={
        "case_payload": {
            "po_id": "PO-CUSTOM-INJ",
            "case_type": "exception",
            "amount": 99999,
            "raw_exception_text": "Custom injected exception",
        }
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["injected_count"] == 1
    assert body["cases"][0]["po_id"] == "PO-CUSTOM-INJ"
    assert body["cases"][0]["amount"] == 99999


def test_simulation_inject_no_scenario_no_payload_400(monkeypatch, tmp_path):
    """Missing both scenario and case_payload should return 400."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.post("/simulation/inject", json={})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Robot Heartbeat / Status
# ---------------------------------------------------------------------------

def test_robot_heartbeat_and_status(monkeypatch, tmp_path):
    """POST /robot/heartbeat saves state, GET /robot/status returns it."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    # Initial status — no heartbeat yet.
    status = client.get("/robot/status").json()
    assert status["status"] == "idle"
    assert status["last_heartbeat_at"] is None

    # Send heartbeat.
    resp = client.post("/robot/heartbeat", json={
        "robot_id": "UIPATH-LOCAL-001",
        "status": "running",
        "current_case_id": "CASE-SIM-003",
        "current_run_id": "RUN-HB-001",
        "message": "processing budget_exceeded",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["robot_id"] == "UIPATH-LOCAL-001"
    assert body["status"] == "running"
    assert body["last_heartbeat_at"] is not None

    # Get status.
    status = client.get("/robot/status").json()
    assert status["robot_id"] == "UIPATH-LOCAL-001"
    assert status["status"] == "running"
    assert status["current_case_id"] == "CASE-SIM-003"
    assert status["current_run_id"] == "RUN-HB-001"
    assert status["last_heartbeat_at"] is not None
    assert "processing" in status["message"]


def test_robot_heartbeat_with_counts(monkeypatch, tmp_path):
    """Heartbeat can include processed_count and failed_count."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    client.post("/robot/heartbeat", json={
        "robot_id": "BOT-001",
        "status": "running",
        "processed_count": 5,
        "failed_count": 1,
    })

    status = client.get("/robot/status").json()
    assert status["processed_count"] == 5
    assert status["failed_count"] == 1


def test_robot_status_derives_counts_from_queue_without_heartbeat(monkeypatch, tmp_path):
    """Without heartbeat, robot status derives counts from simulation queue."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/simulation/cases/next")
    client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "run_id": "RUN-Q-001",
        "result": "SUCCESS",
    })

    status = client.get("/robot/status").json()
    # No heartbeat → derive from queue.
    assert status["processed_count"] == 1  # 1 completed
    assert status["failed_count"] == 0
    assert status["idle_count"] == 9  # 9 pending


def test_robot_heartbeat_idle_transition(monkeypatch, tmp_path):
    """Transition from running to idle increments idle_count."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    client.post("/robot/heartbeat", json={
        "robot_id": "BOT-001",
        "status": "running",
    })
    client.post("/robot/heartbeat", json={
        "robot_id": "BOT-001",
        "status": "idle",
        "message": "queue empty",
    })

    status = client.get("/robot/status").json()
    assert status["status"] == "idle"
    assert status["idle_count"] == 1


# ---------------------------------------------------------------------------
# /monitoring/live
# ---------------------------------------------------------------------------

def test_monitoring_live_returns_200(monkeypatch, tmp_path):
    """Monitoring page should return 200 with all sections."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/monitoring/live")
    assert resp.status_code == 200
    html = resp.text
    assert "Live Monitoring" in html
    assert "Robot Status" in html
    assert "Queue Summary" in html
    assert "Manual Case Injection Panel" in html
    assert "Latest Events / Audit Log" in html
    assert "Real Run Memory" in html
    assert "Proposal Inbox" in html
    assert "Audit Log" in html


def test_monitoring_live_shows_robot_after_heartbeat(monkeypatch, tmp_path):
    """Monitoring page should show robot status after heartbeat."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/robot/heartbeat", json={
        "robot_id": "UIPATH-LOCAL-001",
        "status": "running",
        "current_case_id": "CASE-SIM-001",
        "current_run_id": "RUN-MON-001",
        "message": "processing",
    })

    resp = client.get("/monitoring/live")
    html = resp.text
    assert "UIPATH-LOCAL-001" in html
    assert "CASE-SIM-001" in html
    assert "RUN-MON-001" in html


def test_monitoring_live_shows_completed_case_with_dashboard_link(monkeypatch, tmp_path):
    """Monitoring page should show completed case with dashboard link."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/simulation/cases/next")
    client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "run_id": "RUN-MON-LIVE-001",
        "result": "SUCCESS",
        "final_route": "API_MODE_EXECUTED",
        "policy_decision": "ALLOW_STANDARD_PROCESSING",
        "memory_commit": "COMPLETED",
    })

    resp = client.get("/monitoring/live")
    html = resp.text
    assert "RUN-MON-LIVE-001" in html
    assert "/case-dashboard/CASE-SIM-001?run_id=RUN-MON-LIVE-001" in html


def test_monitoring_live_shows_audit_log_categories(monkeypatch, tmp_path):
    """Monitoring page should show all audit log categories."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/monitoring/live")
    html = resp.text
    assert "normal" in html
    assert "exception" in html
    assert "waiting" in html
    assert "manual" in html
    assert "capability_gap" in html


def test_monitoring_live_no_meta_refresh_uses_js_polling(monkeypatch, tmp_path):
    """Monitoring page must NOT have meta refresh; it should use JS polling instead."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.get("/monitoring/live")
    html = resp.text
    # No meta refresh.
    assert "http-equiv='refresh'" not in html
    assert "content='10'" not in html
    # JS polling present.
    assert "/monitoring/live-data" in html
    assert "fetch('/monitoring/live-data'" in html
    assert "setInterval(refreshMonitoringData" in html
    # Polling indicator and last-updated span present.
    assert "Live data polling: ON" in html
    assert "last-updated" in html


def test_monitoring_live_keeps_polling_and_injection_buttons(monkeypatch, tmp_path):
    """Monitoring owns live polling and injection controls after UX consolidation."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/monitoring/live").text
    assert "fetch('/monitoring/live-data'" in html
    assert "setupInjectionIntercept" in html
    assert "Manual Case Injection Panel" in html
    for label in [
        "Inject Normal Case",
        "Inject Budget Exceeded",
        "Inject Vendor Missing",
        "Inject Inventory Shortage",
        "Inject Ambiguous Case",
    ]:
        assert label in html


def test_monitoring_live_has_stable_dom_ids(monkeypatch, tmp_path):
    """Monitoring page must have stable DOM ids for client-side updates."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.get("/monitoring/live")
    html = resp.text
    for panel_id in [
        "robot-status-panel",
        "queue-summary-panel",
        "latest-cases-panel",
        "real-run-memory-panel",
        "proposal-inbox-panel",
        "audit-log-panel",
        "heartbeat-history-panel",
        "approval-summary-panel",
        "injection-result",
        "last-updated",
    ]:
        assert f"id='{panel_id}'" in html, f"Missing stable DOM id: {panel_id}"


def test_monitoring_live_has_js_injection_intercept(monkeypatch, tmp_path):
    """Monitoring page injection forms must have JS intercept hook."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.get("/monitoring/live")
    html = resp.text
    assert "form.inject-form" in html  # JS selector
    assert "class='inject-form'" in html  # Form class
    assert "data-scenario=" in html  # data attribute
    assert "ev.preventDefault()" in html
    assert "setupInjectionIntercept" in html


# ---------------------------------------------------------------------------
# /monitoring/live-data JSON endpoint
# ---------------------------------------------------------------------------

def test_monitoring_live_data_returns_json(monkeypatch, tmp_path):
    """live-data must return JSON with all required top-level fields."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/monitoring/live-data")
    assert resp.status_code == 200
    data = resp.json()
    # All required top-level fields per spec.
    for field in [
        "robot_status",
        "queue_summary",
        "latest_cases",
        "latest_runs",
        "pattern_counts",
        "proposal_inbox",
        "audit_log",
        "heartbeat_history",
        "approvals_summary",
        "server_time",
    ]:
        assert field in data, f"Missing required field: {field}"

    # queue_summary has the expected shape.
    qs = data["queue_summary"]
    for k in ("pending", "in_progress", "completed", "failed", "total"):
        assert k in qs, f"Missing queue_summary.{k}"

    # approvals_summary has the expected shape.
    ap = data["approvals_summary"]
    for k in ("pending", "approved", "rejected", "total"):
        assert k in ap, f"Missing approvals_summary.{k}"

    # server_time is a non-empty string.
    assert isinstance(data["server_time"], str) and data["server_time"]


def test_monitoring_live_data_is_read_only(monkeypatch, tmp_path):
    """live-data must not change queue state (read-only)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    state_before = client.get("/simulation/state").json()

    # Call live-data multiple times — should not change anything.
    for _ in range(3):
        client.get("/monitoring/live-data")

    state_after = client.get("/simulation/state").json()
    assert state_after["pending"] == state_before["pending"]
    assert state_after["in_progress"] == state_before["in_progress"]
    assert state_after["completed"] == state_before["completed"]
    assert state_after["failed"] == state_before["failed"]
    assert state_after["total"] == state_before["total"]


def test_monitoring_live_data_does_not_write_run_memory(monkeypatch, tmp_path):
    """live-data must not write Run Memory."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    client.get("/monitoring/live-data")
    client.get("/monitoring/live-data")

    runs_dir = tmp_path / "runs"
    assert not runs_dir.exists() or not any(runs_dir.iterdir())


def test_monitoring_live_data_does_not_create_proposal(monkeypatch, tmp_path):
    """live-data must not create proposals."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    client.get("/monitoring/live-data")
    inbox = client.get("/proposals/inbox?format=json").json()
    assert inbox["total"] == 0


def test_monitoring_live_data_reflects_heartbeat(monkeypatch, tmp_path):
    """live-data robot_status should reflect the latest heartbeat."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/robot/heartbeat", json={
        "robot_id": "UIPATH-LOCAL-001",
        "status": "running",
        "current_case_id": "CASE-SIM-XYZ",
        "current_run_id": "RUN-XYZ-001",
        "message": "live-data test",
    })

    data = client.get("/monitoring/live-data").json()
    robot = data["robot_status"]
    assert robot["robot_id"] == "UIPATH-LOCAL-001"
    assert robot["status"] == "running"
    assert robot["current_case_id"] == "CASE-SIM-XYZ"
    assert robot["current_run_id"] == "RUN-XYZ-001"


def test_monitoring_live_data_latest_cases_have_dashboard_url(monkeypatch, tmp_path):
    """live-data latest_cases entries should have dashboard_url (not HTML link)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    data = client.get("/monitoring/live-data").json()
    cases = data["latest_cases"]
    assert len(cases) > 0
    for c in cases:
        assert "dashboard_url" in c
        assert c["dashboard_url"].startswith("/case-dashboard/")
        # Should NOT contain raw HTML.
        assert "<a href" not in c["dashboard_url"]


# ---------------------------------------------------------------------------
# /simulation/dashboard enhancement
# ---------------------------------------------------------------------------

def test_simulation_dashboard_has_quick_links(monkeypatch, tmp_path):
    """Dashboard should have links to monitoring, proposals, etc."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/simulation/dashboard")
    html = resp.text
    assert "/monitoring/live" in html
    assert "/proposals/inbox" in html
    assert "/simulation/inject" in html
    assert "/robot/status" in html


# ---------------------------------------------------------------------------
# /simulation/cases/next enhanced behavior
# ---------------------------------------------------------------------------

def test_simulation_next_has_case_true_when_case_available(monkeypatch, tmp_path):
    """Next should return has_case=true when a case is available."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/simulation/cases/next")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_case"] is True
    assert body["queue_empty"] is False
    assert body["status"] == "in_progress"
    assert body["simulation_case_id"] == "SIM-001"


# ---------------------------------------------------------------------------
# Injection Buttons on /monitoring/live + POST /simulation/inject-form
# ---------------------------------------------------------------------------

def test_monitoring_live_contains_injection_buttons_and_demo_seed_sets(monkeypatch, tmp_path):
    """Monitoring page must contain base injection buttons plus demo seed sets."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/monitoring/live")
    assert resp.status_code == 200
    html = resp.text
    assert "Manual Case Injection Panel" in html
    assert "Inject Normal Case" in html
    assert "Inject Budget Exceeded" in html
    assert "Inject Vendor Missing" in html
    assert "Inject Inventory Shortage" in html
    assert "Inject Ambiguous Case" in html
    assert "Agent Review + Enterprise Context" in html
    assert "API Proposal Seed Set" in html
    assert "XAML Workflow Proposal Seed Set" in html
    assert "data-scenario='agent_context_review'" in html
    assert "data-scenario='capex_budget_exception'" in html
    # All visible injection controls post to /simulation/inject-form.
    assert html.count("action='/simulation/inject-form'") >= 8


def test_inject_form_normal_increases_pending(monkeypatch, tmp_path):
    """POST /simulation/inject-form with scenario=normal should add 1 pending case."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    state_before = client.get("/simulation/state").json()
    pending_before = state_before["pending"]

    # POST form — TestClient follows the 303 redirect by default.
    resp = client.post("/simulation/inject-form", data={
        "scenario": "normal",
        "count": "1",
    }, follow_redirects=True)
    assert resp.status_code == 200
    # Final URL should be /monitoring/live?injected_scenario=normal&injected_count=1
    assert "injected_scenario=normal" in str(resp.url)
    assert "injected_count=1" in str(resp.url)

    # Pending should have increased by 1.
    state_after = client.get("/simulation/state").json()
    assert state_after["pending"] == pending_before + 1

    # The monitoring page should show the injection result.
    assert "Injected 1" in resp.text
    assert "normal" in resp.text


def test_inject_form_budget_exceeded(monkeypatch, tmp_path):
    """POST /simulation/inject-form with scenario=budget_exceeded."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/inject-form", data={
        "scenario": "budget_exceeded",
        "count": "1",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert "injected_scenario=budget_exceeded" in str(resp.url)
    assert "Injected 1" in resp.text
    assert "budget_exceeded" in resp.text


def test_inject_form_inventory_shortage(monkeypatch, tmp_path):
    """POST /simulation/inject-form with scenario=inventory_shortage."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/inject-form", data={
        "scenario": "inventory_shortage",
        "count": "1",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert "injected_scenario=inventory_shortage" in str(resp.url)


def test_inject_form_does_not_write_run_memory(monkeypatch, tmp_path):
    """inject-form must NOT write Run Memory."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    client.post("/simulation/inject-form", data={
        "scenario": "normal",
        "count": "1",
    }, follow_redirects=True)

    # No runs directory should exist in the run memory root.
    runs_dir = tmp_path / "runs"
    assert not runs_dir.exists() or not any(runs_dir.iterdir())


def test_inject_form_does_not_create_proposal(monkeypatch, tmp_path):
    """inject-form must NOT create proposals."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    client.post("/simulation/inject-form", data={
        "scenario": "budget_exceeded",
        "count": "1",
    }, follow_redirects=True)

    inbox = client.get("/proposals/inbox?format=json").json()
    assert inbox["total"] == 0


def test_demo_seed_inject_form_does_not_create_memory_or_proposal(monkeypatch, tmp_path):
    """Demo seed controls enqueue repeated cases; they do not create proposals directly."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/inject-form", data={
        "scenario": "capex_budget_exception",
        "count": "3",
    }, follow_redirects=True)

    assert resp.status_code == 200
    state = client.get("/simulation/state").json()
    capex_cases = [c for c in state["cases"] if c.get("scenario") == "capex_budget_exception"]
    assert len(capex_cases) == 3
    assert all(c["status"] == "pending" for c in capex_cases)
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())
    assert client.get("/proposals/inbox?format=json").json()["total"] == 0


def test_inject_form_unknown_scenario_returns_400(monkeypatch, tmp_path):
    """Unknown scenario via form should return 400."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    resp = client.post("/simulation/inject-form", data={
        "scenario": "bogus",
        "count": "1",
    }, follow_redirects=False)
    assert resp.status_code == 400


def test_inject_form_redirect_is_303(monkeypatch, tmp_path):
    """inject-form should return 303 redirect (not 200 or 302)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/inject-form", data={
        "scenario": "normal",
        "count": "1",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert "/monitoring/live" in resp.headers["location"]


def test_monitoring_live_shows_injection_queue_guidance(monkeypatch, tmp_path):
    """Monitoring page should explain that injected cases enter the work queue."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    resp = client.get("/monitoring/live")
    html = resp.text
    assert "add cases to the processing queue" in html
    assert "UiPath Robot picks up queued cases in Worker Mode" in html


def test_monitoring_live_injected_case_shows_pending(monkeypatch, tmp_path):
    """After injection, the new case should appear as pending in Latest Cases."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # Inject a budget_exceeded case.
    client.post("/simulation/inject-form", data={
        "scenario": "budget_exceeded",
        "count": "1",
    }, follow_redirects=True)

    # The monitoring page should show the new pending case.
    resp = client.get("/monitoring/live")
    html = resp.text
    assert "pending" in html.lower()
    # The injected case should appear in the Latest Cases table.
    assert "PO-INJ" in html
