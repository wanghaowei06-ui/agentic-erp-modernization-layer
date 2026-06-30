"""Tests for the Legacy ERP Work Queue + ERP action buttons.

Covers:
  - GET /erp/work-queue — HTML table of all simulation cases
  - GET /erp/work-queue/{simulation_case_id} — detail page with stable legacy DOM ids
  - 5 ERP action button POST endpoints — update erp_status/last_action only
  - /monitoring/live has link to /erp/work-queue
  - ERP actions do NOT write Run Memory, create proposals, or call Codex
"""
from __future__ import annotations

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


# ---------------------------------------------------------------------------
# GET /erp/work-queue
# ---------------------------------------------------------------------------

def test_erp_work_queue_returns_200_with_cases(monkeypatch, tmp_path):
    """ERP work queue page should return 200 and show all simulation cases."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/erp/work-queue")
    assert resp.status_code == 200
    html = resp.text
    assert "Legacy ERP" in html
    assert "Purchase Order Work Queue" in html
    # Should show all 10 default cases.
    assert "PO-SIM-001" in html
    assert "PO-SIM-010" in html
    # Table headers.
    assert "PO Number" in html
    assert "ERP Status" in html
    assert "Simulation Status" in html
    assert "Last Action" in html
    assert "Open" in html


def test_erp_work_queue_shows_injected_cases(monkeypatch, tmp_path):
    """After injecting normal and budget_exceeded cases, they should appear."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    client.post("/simulation/inject", json={"scenario": "normal", "count": 1})
    client.post("/simulation/inject", json={"scenario": "budget_exceeded", "count": 1})

    resp = client.get("/erp/work-queue")
    html = resp.text
    assert "PO-INJ" in html
    assert "budget_exceeded" in html


def test_erp_work_queue_pending_shown_first(monkeypatch, tmp_path):
    """Pending cases should appear before completed cases."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # Complete first case.
    client.get("/simulation/cases/next")  # marks SIM-001 as in_progress
    client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "result": "SUCCESS",
        "final_route": "API_MODE_EXECUTED",
        "memory_commit": "COMPLETED",
    })

    resp = client.get("/erp/work-queue")
    html = resp.text
    # SIM-002 (pending) should appear before SIM-001 (completed).
    pos_002 = html.find("SIM-002")
    pos_001 = html.find("SIM-001")
    assert pos_002 > 0
    assert pos_001 > 0
    assert pos_002 < pos_001


def test_erp_work_queue_has_open_links(monkeypatch, tmp_path):
    """Each row should have an Open link to the detail page."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/erp/work-queue")
    html = resp.text
    assert "/erp/work-queue/SIM-001" in html
    assert "/erp/work-queue/SIM-010" in html


def test_erp_work_queue_open_links_have_webforms_ids(monkeypatch, tmp_path):
    """Open links must use WebForms GridView-style ids (ctl00_MainContent_grdPoWorkQueue_ctlNN_btnOpen)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/erp/work-queue")
    html = resp.text
    # First data row is ctl02, second is ctl03, etc.
    assert "id='ctl00_MainContent_grdPoWorkQueue_ctl02_btnOpen'" in html
    assert "data-uipath-role='open-case'" in html
    # No unsafe hyphenated id should appear.
    assert "id='btnOpen_SIM-001'" not in html
    assert "id='btnOpen_SIM_001'" not in html


def test_erp_work_queue_has_description_text(monkeypatch, tmp_path):
    """Work queue page should have the legacy-style description text."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/erp/work-queue")
    html = resp.text
    assert "mirrored from real simulation queue" in html
    assert "UiPath UI automation proof" in html


def test_erp_work_queue_has_grdPoWorkQueue(monkeypatch, tmp_path):
    """Work queue table must have stable WebForms id ctl00_MainContent_grdPoWorkQueue."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/erp/work-queue")
    html = resp.text
    assert "id='ctl00_MainContent_grdPoWorkQueue'" in html


def test_erp_work_queue_empty_shows_queueEmptyMessage(monkeypatch, tmp_path):
    """Empty queue shows ctl00_MainContent_lblQueueEmptyMessage, no btnOpenFirstPending."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    # Do NOT reset — queue is empty by default after fresh app load.
    resp = client.get("/erp/work-queue")
    html = resp.text
    assert "id='ctl00_MainContent_lblQueueEmptyMessage'" in html
    assert "No pending ERP work item." in html
    assert "ctl00_MainContent_btnOpenFirstPending" not in html


def test_erp_work_queue_has_btnOpenFirstPending_after_reset(monkeypatch, tmp_path):
    """After reset (pending cases exist), page shows ctl00_MainContent_btnOpenFirstPending."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/erp/work-queue")
    html = resp.text
    assert "id='ctl00_MainContent_btnOpenFirstPending'" in html
    assert "Open First Pending Case" in html
    # queueEmptyMessage should NOT appear when pending cases exist.
    assert "id='ctl00_MainContent_lblQueueEmptyMessage'" not in html


def test_erp_work_queue_btnOpenFirstPending_points_to_first_pending(monkeypatch, tmp_path):
    """ctl00_MainContent_btnOpenFirstPending href must point to the first pending case detail page."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/erp/work-queue")
    html = resp.text
    # First pending case after reset is SIM-001 (normal, pending).
    assert "id='ctl00_MainContent_btnOpenFirstPending'" in html
    assert "href='/erp/work-queue/SIM-001'" in html


def test_erp_work_queue_btnOpenFirstPending_after_inject(monkeypatch, tmp_path):
    """After injecting a normal case into empty queue, ctl00_MainContent_btnOpenFirstPending appears."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    # Queue is empty initially.
    resp = client.get("/erp/work-queue")
    assert "ctl00_MainContent_btnOpenFirstPending" not in resp.text

    # Inject a normal case.
    client.post("/simulation/inject", json={"scenario": "normal", "count": 1})
    resp = client.get("/erp/work-queue")
    html = resp.text
    assert "id='ctl00_MainContent_btnOpenFirstPending'" in html
    assert "Open First Pending Case" in html


def test_erp_work_queue_has_legacy_erp_style(monkeypatch, tmp_path):
    """Page should contain legacy ERP style keywords: top nav, module menu, breadcrumb."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/erp/work-queue")
    html = resp.text
    assert "Legacy ERP" in html
    assert "Purchase Order Work Queue" in html
    assert "Module Menu" in html
    assert "Contoso Legacy ERP 2009" in html
    assert "top-shell" in html
    assert "module-menu" in html
    assert "breadcrumb-bar" in html
    assert "legacy-footer" in html
    assert "Legacy WebForms compatibility mode" in html


# ---------------------------------------------------------------------------
# GET /erp/work-queue/{simulation_case_id}
# ---------------------------------------------------------------------------

def test_erp_detail_page_has_stable_legacy_ids(monkeypatch, tmp_path):
    """Detail page must have all 14 stable legacy-style DOM ids for UiPath selectors."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/erp/work-queue/SIM-003")
    assert resp.status_code == 200
    html = resp.text
    # Required stable legacy ids (14 total).
    for dom_id in [
        "ctl00_MainContent_lblSimulationCaseId",
        "ctl00_MainContent_lblCaseId",
        "ctl00_MainContent_lblPoNumber",
        "ctl00_MainContent_lblAmount",
        "ctl00_MainContent_lblBudgetLimit",
        "ctl00_MainContent_lblVendorId",
        "ctl00_MainContent_lblScenario",
        "ctl00_MainContent_lblExceptionReason",
        "ctl00_MainContent_lblErpStatus",
        "ctl00_MainContent_lblSimulationStatus",
        "ctl00_MainContent_lblRunId",
        "ctl00_MainContent_lblFinalRoute",
        "ctl00_MainContent_lblPolicyDecision",
        "ctl00_MainContent_lblLastAction",
        "ctl00_MainContent_lblBusinessRemarks",
    ]:
        assert dom_id in html, f"Missing stable legacy DOM id: {dom_id}"

    # Content should match the case.
    assert "PO-SIM-003" in html
    assert "18000" in html  # amount
    assert "budget_exceeded" in html  # scenario


def test_erp_detail_page_moves_internal_fields_to_technical_audit(monkeypatch, tmp_path):
    """Business fields should be primary; demo/internal selectors stay in a collapsed audit area."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    html = client.get("/erp/work-queue/SIM-003").text

    assert "Purchase Order Processing" in html
    assert "System Message" in html
    assert "Business Remarks" in html
    assert "Q4 customer delivery is at risk" in html
    assert "Technical Audit / RPA Metadata" in html
    assert html.find("Business Remarks") < html.find("Technical Audit / RPA Metadata")


def test_erp_detail_page_404_for_unknown(monkeypatch, tmp_path):
    """Detail page for unknown simulation_case_id returns 404."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.get("/erp/work-queue/SIM-FAKE-9999")
    assert resp.status_code == 404


def test_erp_detail_page_has_5_action_buttons(monkeypatch, tmp_path):
    """Detail page must have all 5 ERP action buttons with WebForms-style ids and text."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/erp/work-queue/SIM-001")
    html = resp.text
    # Button display text.
    assert "Mark Standard Processed" in html
    assert "Mark Waiting Vendor" in html
    assert "Flag Capability Gap" in html
    assert "Send Manual Investigation" in html
    assert "Submit Approval Request" in html
    # Form actions.
    assert "/mark-standard-processed" in html
    assert "/mark-waiting-vendor" in html
    assert "/flag-capability-gap" in html
    assert "/send-manual-investigation" in html
    assert "/submit-approval-request" in html
    # WebForms-style button ids for UiPath selectors.
    assert "id='ctl00_MainContent_btnMarkStandardProcessed'" in html
    assert "id='ctl00_MainContent_btnMarkWaitingVendor'" in html
    assert "id='ctl00_MainContent_btnFlagCapabilityGap'" in html
    assert "id='ctl00_MainContent_btnSendManualInvestigation'" in html
    assert "id='ctl00_MainContent_btnSubmitApprovalRequest'" in html


# ---------------------------------------------------------------------------
# ERP action button endpoints
# ---------------------------------------------------------------------------

def test_erp_mark_standard_processed(monkeypatch, tmp_path):
    """mark-standard-processed updates erp_status and last_action."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/erp/work-queue/SIM-001/mark-standard-processed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["erp_status"] == "ERP_STANDARD_PROCESSED"
    assert body["last_action"] == "MARK_STANDARD_PROCESSED"
    assert body["run_memory_written"] is False
    assert body["proposal_created"] is False
    assert body["codex_called"] is False


def test_erp_mark_waiting_vendor(monkeypatch, tmp_path):
    """mark-waiting-vendor updates erp_status."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/erp/work-queue/SIM-002/mark-waiting-vendor")
    assert resp.status_code == 200
    assert resp.json()["erp_status"] == "ERP_WAITING_VENDOR_INFO"
    assert resp.json()["last_action"] == "MARK_WAITING_VENDOR"


def test_erp_flag_capability_gap(monkeypatch, tmp_path):
    """flag-capability-gap updates erp_status."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/erp/work-queue/SIM-003/flag-capability-gap")
    assert resp.status_code == 200
    assert resp.json()["erp_status"] == "ERP_CAPABILITY_GAP_FLAGGED"
    assert resp.json()["last_action"] == "FLAG_CAPABILITY_GAP"


def test_erp_send_manual_investigation(monkeypatch, tmp_path):
    """send-manual-investigation updates erp_status."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/erp/work-queue/SIM-004/send-manual-investigation")
    assert resp.status_code == 200
    assert resp.json()["erp_status"] == "ERP_MANUAL_INVESTIGATION_REQUIRED"


def test_erp_submit_approval_request(monkeypatch, tmp_path):
    """submit-approval-request updates ERP state and creates a pending approval."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/erp/work-queue/SIM-005/submit-approval-request")
    assert resp.status_code == 200
    body = resp.json()
    assert body["erp_status"] == "ERP_APPROVAL_REQUESTED"
    assert body["last_action"] == "SUBMIT_APPROVAL_REQUEST"
    assert body["approval_created"] is True
    assert body["approval_id"].startswith("APR-")
    assert body["approval_status"] == "PENDING"
    assert body["approval_inbox_url"] == "/approvals/inbox"

    inbox = client.get("/approvals/inbox").text
    assert "Human Approval Inbox" in inbox
    assert body["approval_id"] in inbox
    assert "CASE-SIM-005" in inbox
    assert "PENDING" in inbox


def test_erp_submit_approval_request_is_idempotent_for_same_case(monkeypatch, tmp_path):
    """Repeated Submit Approval Request clicks should not create duplicates."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    first = client.post("/erp/work-queue/SIM-005/submit-approval-request").json()
    second = client.post("/erp/work-queue/SIM-005/submit-approval-request").json()

    assert first["approval_id"] == second["approval_id"]
    assert second["approval_created"] is False
    inbox = client.get("/approvals/inbox").text
    assert "1 pending · 1 total" in inbox


def test_erp_action_404_for_unknown_case(monkeypatch, tmp_path):
    """ERP action on unknown simulation_case_id returns 404."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.post("/erp/work-queue/SIM-FAKE-9999/mark-standard-processed")
    assert resp.status_code == 404


def test_erp_action_does_not_write_run_memory(monkeypatch, tmp_path):
    """ERP action buttons must NOT write Run Memory."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    client.post("/erp/work-queue/SIM-001/mark-standard-processed")
    client.post("/erp/work-queue/SIM-002/submit-approval-request")

    runs_dir = tmp_path / "runs"
    assert not runs_dir.exists() or not any(runs_dir.iterdir())


@pytest.mark.parametrize("action", [
    "mark-standard-processed",
    "mark-waiting-vendor",
    "flag-capability-gap",
    "send-manual-investigation",
])
def test_non_approval_erp_actions_do_not_create_approvals(monkeypatch, tmp_path, action):
    """Only Submit Approval Request should create a Human Approval task."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post(f"/erp/work-queue/SIM-001/{action}")
    assert resp.status_code == 200
    body = resp.json()
    assert "approval_id" not in body
    assert "approval_created" not in body

    inbox = client.get("/approvals/inbox").text
    assert "No approval tasks" in inbox


def test_erp_action_does_not_create_proposal(monkeypatch, tmp_path):
    """ERP action buttons must NOT create proposals."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    for sim_id in ["SIM-001", "SIM-002", "SIM-003", "SIM-004", "SIM-005"]:
        client.post(f"/erp/work-queue/{sim_id}/submit-approval-request")

    inbox = client.get("/proposals/inbox?format=json").json()
    assert inbox["total"] == 0


def test_erp_action_updates_visible_on_detail_page(monkeypatch, tmp_path):
    """After an ERP action, the detail page should show the updated erp_status."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    client.post("/erp/work-queue/SIM-003/submit-approval-request")

    resp = client.get("/erp/work-queue/SIM-003")
    html = resp.text
    assert "ERP_APPROVAL_REQUESTED" in html
    assert "SUBMIT_APPROVAL_REQUEST" in html


def test_erp_action_updates_visible_on_work_queue(monkeypatch, tmp_path):
    """After an ERP action, the work queue table should show the updated status."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    client.post("/erp/work-queue/SIM-003/flag-capability-gap")

    resp = client.get("/erp/work-queue")
    html = resp.text
    assert "ERP_CAPABILITY_GAP_FLAGGED" in html


# ---------------------------------------------------------------------------
# /monitoring/live link to ERP work queue
# ---------------------------------------------------------------------------

def test_monitoring_live_has_erp_work_queue_link(monkeypatch, tmp_path):
    """Monitoring page should have links to ERP work queue, approved-pending-writeback, and approval inbox."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/monitoring/live")
    html = resp.text
    assert "/erp/work-queue" in html
    assert "Legacy ERP Work Queue" in html
    assert "/approvals/approved-pending-writeback" in html
    assert "Approved Pending ERP Writeback" in html
    assert "/approvals/inbox" in html
    assert "Approval Inbox" in html
    # Prominent link near Injection Panel with full URL.
    assert "http://localhost:8002/erp/work-queue" in html
    assert "ctl00_MainContent_btnOpenFirstPending" in html


def test_erp_work_queue_no_fixed_huge_height(monkeypatch, tmp_path):
    """Page should use flex layout with internal scrolling, not fixed huge heights."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/erp/work-queue")
    html = resp.text
    # Flex layout key classes.
    assert "erp-shell" in html
    assert "erp-body" in html
    assert "module-menu" in html
    assert "erp-content-wrap" in html
    # Content-wrap scrolls internally (not body).
    assert "overflow: auto" in html
    # Should NOT have fixed grid-template-rows with huge heights.
    assert "grid-template-rows: 28px minmax(0, 1fr) 29px" not in html


def test_simulation_dashboard_has_erp_work_queue_link(monkeypatch, tmp_path):
    """Simulation dashboard quick links should include /erp/work-queue."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/simulation/dashboard")
    html = resp.text
    assert "/erp/work-queue" in html
    assert "Legacy ERP Work Queue" in html
    assert "/approvals/approved-pending-writeback" in html


# ===========================================================================
# Claim-on-open: GET /erp/work-queue/{id} claims the simulation case
# ===========================================================================

def _get_case_status(client, sim_id: str) -> str:
    """Helper: read a simulation case's status from /simulation/state."""
    state = client.get("/simulation/state").json()
    for c in state.get("cases", []):
        if c.get("simulation_case_id") == sim_id:
            return c.get("status", "")
    return ""


def test_detail_page_claims_pending_case(monkeypatch, tmp_path):
    """A. Opening ERP detail page should claim the case (pending → in_progress)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # SIM-001 should be pending initially.
    assert _get_case_status(client, "SIM-001") == "pending"

    # Open the detail page.
    resp = client.get("/erp/work-queue/SIM-001")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")

    # SIM-001 should now be in_progress.
    assert _get_case_status(client, "SIM-001") == "in_progress"


def test_detail_page_claim_is_idempotent(monkeypatch, tmp_path):
    """B. Opening detail page for an already in_progress case should be idempotent."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # First open claims the case.
    client.get("/erp/work-queue/SIM-001")
    assert _get_case_status(client, "SIM-001") == "in_progress"

    # Second open should not error and status should stay in_progress.
    resp = client.get("/erp/work-queue/SIM-001")
    assert resp.status_code == 200
    assert _get_case_status(client, "SIM-001") == "in_progress"


def test_detail_page_does_not_reclaim_completed_case(monkeypatch, tmp_path):
    """C. Opening detail page for a completed case should not re-claim it."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # Claim and complete SIM-001.
    client.post("/simulation/cases/SIM-001/claim")
    client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "result": "SUCCESS",
        "final_route": "API_MODE_EXECUTED",
        "memory_commit": "COMPLETED",
    })
    assert _get_case_status(client, "SIM-001") == "completed"

    # Open detail page — should show the page but NOT re-claim.
    resp = client.get("/erp/work-queue/SIM-001")
    assert resp.status_code == 200
    assert _get_case_status(client, "SIM-001") == "completed"


def test_detail_page_404_for_unknown_case(monkeypatch, tmp_path):
    """Opening detail page for unknown case returns 404."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.get("/erp/work-queue/SIM-FAKE-9999")
    assert resp.status_code == 404


# ===========================================================================
# POST /simulation/cases/{id}/claim — explicit claim endpoint
# ===========================================================================

def test_claim_endpoint_pending_to_in_progress(monkeypatch, tmp_path):
    """D. POST /simulation/cases/{id}/claim: pending → in_progress, claimed=True."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/simulation/cases/SIM-001/claim")
    assert resp.status_code == 200
    body = resp.json()
    assert body["simulation_case_id"] == "SIM-001"
    assert body["status"] == "in_progress"
    assert body["claimed"] is True


def test_claim_endpoint_idempotent_for_in_progress(monkeypatch, tmp_path):
    """D. POST /simulation/cases/{id}/claim: in_progress → stays in_progress, claimed=False."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # First claim.
    client.post("/simulation/cases/SIM-001/claim")

    # Second claim should be idempotent.
    resp = client.post("/simulation/cases/SIM-001/claim")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "in_progress"
    assert body["claimed"] is False


def test_claim_endpoint_404_for_unknown(monkeypatch, tmp_path):
    """D. POST /simulation/cases/{id}/claim: unknown id → 404."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.post("/simulation/cases/SIM-FAKE-9999/claim")
    assert resp.status_code == 404


def test_claim_endpoint_does_not_reset_completed(monkeypatch, tmp_path):
    """D. POST /simulation/cases/{id}/claim: completed → not re-claimed."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # Claim and complete.
    client.post("/simulation/cases/SIM-001/claim")
    client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "result": "SUCCESS",
        "memory_commit": "COMPLETED",
    })

    # Try to claim again — should not reset.
    resp = client.post("/simulation/cases/SIM-001/claim")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["claimed"] is False


def test_claim_allows_complete_without_next(monkeypatch, tmp_path):
    """After claiming via /claim, /simulation/cases/complete should succeed
    without ever calling /simulation/cases/next."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # Claim SIM-002 directly (not the first in queue).
    client.post("/simulation/cases/SIM-002/claim")
    assert _get_case_status(client, "SIM-002") == "in_progress"

    # Complete should work.
    resp = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-002",
        "result": "SUCCESS",
        "memory_commit": "COMPLETED",
    })
    assert resp.status_code == 200
    assert _get_case_status(client, "SIM-002") == "completed"


# ===========================================================================
# ERP action form-redirect endpoints
# ===========================================================================

def test_erp_action_form_redirects_to_work_queue(monkeypatch, tmp_path):
    """E. ERP action -form endpoint should redirect (303), not return JSON."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/erp/work-queue/SIM-001/send-manual-investigation-form",
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "/erp/work-queue" in resp.headers.get("location", "")


@pytest.mark.parametrize("action", [
    "mark-standard-processed",
    "mark-waiting-vendor",
    "flag-capability-gap",
    "send-manual-investigation",
    "submit-approval-request",
])
def test_all_erp_action_form_endpoints_redirect_303(monkeypatch, tmp_path, action):
    """All browser form endpoints redirect back to the HTML queue."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post(f"/erp/work-queue/SIM-001/{action}-form",
                       follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers.get("location") == "/erp/work-queue"


def test_erp_action_form_executes_side_effect(monkeypatch, tmp_path):
    """E. ERP action -form endpoint should still execute the business side effect."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    client.post("/erp/work-queue/SIM-001/flag-capability-gap-form",
                follow_redirects=False)

    # Verify the side effect by checking the detail page.
    html = client.get("/erp/work-queue/SIM-001").text
    assert "ERP_CAPABILITY_GAP_FLAGGED" in html
    assert "FLAG_CAPABILITY_GAP" in html


def test_submit_approval_request_form_creates_human_approval(monkeypatch, tmp_path):
    """The UiPath-facing form button should create a pending approval task."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/erp/work-queue/SIM-005/submit-approval-request-form",
                       follow_redirects=False)

    assert resp.status_code == 303
    assert resp.headers.get("location") == "/erp/work-queue"
    inbox = client.get("/approvals/inbox").text
    assert "Human Approval Inbox" in inbox
    assert "1 pending · 1 total" in inbox
    assert "CASE-SIM-005" in inbox
    assert "PENDING" in inbox


def test_erp_action_form_404_for_unknown_case(monkeypatch, tmp_path):
    """E. ERP action -form for unknown case should return 404."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.post("/erp/work-queue/SIM-FAKE-9999/mark-standard-processed-form",
                       follow_redirects=False)
    assert resp.status_code == 404


def test_erp_action_json_endpoints_still_return_json(monkeypatch, tmp_path):
    """Original JSON endpoints (without -form) should still return JSON."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.post("/erp/work-queue/SIM-001/mark-standard-processed")
    assert resp.status_code == 200
    assert "application/json" in resp.headers.get("content-type", "")
    body = resp.json()
    assert body["erp_status"] == "ERP_STANDARD_PROCESSED"


def test_detail_page_forms_point_to_form_endpoints(monkeypatch, tmp_path):
    """Detail page form actions should use -form suffix for browser redirect."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    html = client.get("/erp/work-queue/SIM-001").text
    for action in ["mark-standard-processed", "mark-waiting-vendor",
                   "flag-capability-gap", "send-manual-investigation",
                   "submit-approval-request"]:
        assert f"/erp/work-queue/SIM-001/{action}-form" in html, \
            f"Form action should point to -form endpoint: {action}"


def test_detail_page_preserves_button_ids_with_form_endpoints(monkeypatch, tmp_path):
    """Button ids should be preserved even with -form action endpoints."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    html = client.get("/erp/work-queue/SIM-001").text
    for btn_id in [
        "ctl00_MainContent_btnMarkStandardProcessed",
        "ctl00_MainContent_btnMarkWaitingVendor",
        "ctl00_MainContent_btnFlagCapabilityGap",
        "ctl00_MainContent_btnSendManualInvestigation",
        "ctl00_MainContent_btnSubmitApprovalRequest",
    ]:
        assert btn_id in html, f"Missing button id: {btn_id}"


# ===========================================================================
# Regression: /simulation/cases/next still works, /complete still requires in_progress
# ===========================================================================

def test_simulation_next_still_works(monkeypatch, tmp_path):
    """GET /simulation/cases/next should still claim the next pending case."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/simulation/cases/next")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_case"] is True
    assert body["simulation_case_id"] == "SIM-001"
    assert _get_case_status(client, "SIM-001") == "in_progress"


def test_complete_rejects_pending_case(monkeypatch, tmp_path):
    """POST /simulation/cases/complete should reject a pending case (no claim)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # SIM-001 is pending — complete should fail with 400.
    resp = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "result": "SUCCESS",
        "memory_commit": "COMPLETED",
    })
    assert resp.status_code == 400


def test_complete_works_after_claim(monkeypatch, tmp_path):
    """POST /simulation/cases/complete should work after claiming via /claim."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # Claim via the new endpoint.
    client.post("/simulation/cases/SIM-003/claim")

    # Now complete should work.
    resp = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-003",
        "result": "SUCCESS",
        "memory_commit": "COMPLETED",
    })
    assert resp.status_code == 200
    assert _get_case_status(client, "SIM-003") == "completed"


def test_action_then_complete_advances_first_pending(monkeypatch, tmp_path):
    """Opening, clicking an ERP action, and completing moves the queue to SIM-002."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    html = client.get("/erp/work-queue").text
    assert "href='/erp/work-queue/SIM-001'" in html

    # Opening detail claims SIM-001 before the ERP action.
    detail = client.get("/erp/work-queue/SIM-001")
    assert detail.status_code == 200
    assert _get_case_status(client, "SIM-001") == "in_progress"

    action = client.post("/erp/work-queue/SIM-001/send-manual-investigation-form",
                         follow_redirects=False)
    assert action.status_code == 303
    run = client.post("/memory/runs/start", json={
        "case_id": "CASE-SIM-001",
        "po_id": "PO-SIM-001",
        "workflow_name": "Main.xaml",
        "source": "uipath_rpa_erp_worker",
        "demo_mode": True,
    }).json()

    complete = client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "case_id": "CASE-SIM-001",
        "po_id": "PO-SIM-001",
        "run_id": run["run_id"],
        "result": "ERP_MANUAL_INVESTIGATION_REQUIRED",
        "final_route": "MANUAL_INVESTIGATION",
        "policy_decision": "REQUIRE_MANUAL_INVESTIGATION",
        "memory_commit": "SKIPPED",
    })
    assert complete.status_code == 200
    assert complete.json()["status"] == "completed"

    html = client.get("/erp/work-queue").text
    assert "id='ctl00_MainContent_btnOpenFirstPending'" in html
    assert "href='/erp/work-queue/SIM-002'" in html
    assert "points to SIM-002" in html


def test_completed_case_not_selected_as_first_pending(monkeypatch, tmp_path):
    """Completed cases remain visible but are not the first pending target."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")
    client.get("/erp/work-queue/SIM-001")
    client.post("/simulation/cases/complete", json={
        "simulation_case_id": "SIM-001",
        "result": "ERP_STANDARD_PROCESSED",
        "memory_commit": "COMPLETED",
    })

    html = client.get("/erp/work-queue").text
    assert "SIM-001" in html
    assert "completed" in html
    assert "href='/erp/work-queue/SIM-002'" in html
    assert "points to SIM-001" not in html


def test_queue_empty_when_all_cases_terminal(monkeypatch, tmp_path):
    """When no pending/waiting cases remain, show the stable empty selector."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    for i in range(1, 11):
        sim_id = f"SIM-{i:03d}"
        client.post(f"/simulation/cases/{sim_id}/claim")
        resp = client.post("/simulation/cases/complete", json={
            "simulation_case_id": sim_id,
            "result": "ERP_STANDARD_PROCESSED",
            "memory_commit": "COMPLETED",
        })
        assert resp.status_code == 200

    html = client.get("/erp/work-queue").text
    assert "id='ctl00_MainContent_lblQueueEmptyMessage'" in html
    assert "No pending ERP work item." in html
    assert "ctl00_MainContent_btnOpenFirstPending" not in html
