"""Tests for the unified Legacy ERP Shell visual conversion.

Covers:
  - All 6 pages use the same _render_legacy_shell() helper
  - Each page contains "Contoso Legacy ERP 2009", Module Menu, breadcrumb, footer
  - Flex layout classes present: erp-shell, erp-body, module-menu, erp-content-wrap
  - Top tab navigation with 5 tabs and active highlighting
  - /proposals/inbox returns HTML by default, ?format=json returns JSON
  - /monitoring/live preserves live-data fetch script and all DOM ids
  - /approvals/inbox has Human Approval Inbox content
  - /erp/work-queue selectors preserved (ctl00_MainContent_*)
  - No dark terminal styling (#0d1117) remains on converted pages
  - No meta refresh on /monitoring/live and /approvals/inbox
"""
from __future__ import annotations

import sys
from pathlib import Path

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
# Shared shell elements present on ALL legacy pages
# ---------------------------------------------------------------------------

LEGACY_PAGES = [
    ("/erp/work-queue", "Procurement"),
    ("/approvals/inbox", "Approvals"),
    ("/monitoring/live", "Monitoring"),
    ("/simulation/dashboard", "Simulation"),
    ("/proposals/inbox", "Proposals"),
]

FLEX_CLASSES = ["erp-shell", "erp-body", "module-menu", "erp-content-wrap"]
LEGACY_TABS = ["Procurement", "Approvals", "Monitoring", "Simulation", "Proposals"]


def _setup_queue(client):
    """Reset simulation queue so ERP work-queue has cases to show."""
    client.post("/simulation/reset")


def test_all_legacy_pages_have_contoso_title(monkeypatch, tmp_path):
    """Every legacy page must contain 'Contoso Legacy ERP 2009' in the top shell."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    for path, _tab in LEGACY_PAGES:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"
        html = resp.text
        assert "Contoso Legacy ERP 2009" in html, f"{path} missing Contoso title"


def test_all_legacy_pages_have_module_menu(monkeypatch, tmp_path):
    """Every legacy page must have a Module Menu section."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    for path, _tab in LEGACY_PAGES:
        html = client.get(path).text
        assert "Module Menu" in html, f"{path} missing Module Menu"


def test_all_legacy_pages_have_flex_layout_classes(monkeypatch, tmp_path):
    """Every legacy page CSS must include flex layout classes."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    for path, _tab in LEGACY_PAGES:
        html = client.get(path).text
        for cls in FLEX_CLASSES:
            assert cls in html, f"{path} missing flex class '{cls}'"


def test_all_legacy_pages_have_top_tabs(monkeypatch, tmp_path):
    """Every legacy page must have all 5 top navigation tabs."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    for path, _tab in LEGACY_PAGES:
        html = client.get(path).text
        for tab in LEGACY_TABS:
            assert f">{tab}</a>" in html, f"{path} missing tab '{tab}'"


def test_all_legacy_pages_have_breadcrumb_and_footer(monkeypatch, tmp_path):
    """Every legacy page must have breadcrumb-bar and legacy-footer."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    for path, _tab in LEGACY_PAGES:
        html = client.get(path).text
        assert "breadcrumb-bar" in html, f"{path} missing breadcrumb-bar"
        assert "legacy-footer" in html, f"{path} missing legacy-footer"
        assert "Legacy WebForms compatibility mode" in html, f"{path} missing footer text"


def test_all_legacy_pages_have_screen_id(monkeypatch, tmp_path):
    """Every legacy page must have a Screen ID in the footer."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    for path, _tab in LEGACY_PAGES:
        html = client.get(path).text
        assert "Screen ID:" in html, f"{path} missing Screen ID"


def test_active_tab_highlighted_on_each_page(monkeypatch, tmp_path):
    """Each page must highlight its active tab with class='active'."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    for path, tab in LEGACY_PAGES:
        html = client.get(path).text
        assert f"class='active'>{tab}</a>" in html, f"{path} tab '{tab}' not marked active"


# ---------------------------------------------------------------------------
# No dark terminal styling remains
# ---------------------------------------------------------------------------

def test_no_dark_terminal_bg_on_legacy_pages(monkeypatch, tmp_path):
    """Converted pages must not have the old dark terminal background (#0d1117)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    for path, _tab in LEGACY_PAGES:
        html = client.get(path).text
        assert "#0d1117" not in html, f"{path} still has dark terminal background"


def test_no_meta_refresh_on_monitoring_live(monkeypatch, tmp_path):
    """/monitoring/live must not have meta refresh (uses JS polling instead)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/monitoring/live").text
    assert "http-equiv='refresh'" not in html
    assert "http-equiv=\"refresh\"" not in html


def test_no_meta_refresh_on_approvals_inbox(monkeypatch, tmp_path):
    """/approvals/inbox must not have meta refresh."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/approvals/inbox").text
    assert "http-equiv='refresh'" not in html
    assert "http-equiv=\"refresh\"" not in html


# ---------------------------------------------------------------------------
# /approvals/inbox legacy shell
# ---------------------------------------------------------------------------

def test_approvals_inbox_has_human_approval_title(monkeypatch, tmp_path):
    """/approvals/inbox must show 'Human Approval Inbox' as the page title."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/approvals/inbox").text
    assert "Human Approval Inbox" in html


def test_approvals_inbox_has_legacy_shell_elements(monkeypatch, tmp_path):
    """/approvals/inbox must have Contoso title, Module Menu, breadcrumb."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/approvals/inbox").text
    assert "Contoso Legacy ERP 2009" in html
    assert "Module Menu" in html
    assert "breadcrumb-bar" in html
    assert "legacy-footer" in html


def test_approvals_inbox_shows_pending_total(monkeypatch, tmp_path):
    """/approvals/inbox must show pending and total counts."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    # Create an approval task.
    client.post("/approvals/create", json={
        "case_id": "CASE-TEST-001",
        "po_id": "PO-TEST-001",
        "amount": 5000,
        "budget_limit": 3000,
        "reason": "Budget exceeded",
    })
    html = client.get("/approvals/inbox").text
    assert "pending" in html
    assert "total" in html


def test_approvals_inbox_has_approve_reject_forms(monkeypatch, tmp_path):
    """PENDING approval tasks must have Approve and Reject forms."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.post("/approvals/create", json={
        "case_id": "CASE-FORM-001",
        "po_id": "PO-FORM-001",
        "amount": 5000,
        "budget_limit": 3000,
    })
    approval_id = resp.json()["approval_id"]
    html = client.get("/approvals/inbox").text
    assert f"/approvals/{approval_id}/approve" in html
    assert f"/approvals/{approval_id}/reject" in html
    assert "Approve" in html
    assert "Reject" in html
    assert ".approval-actions { display: grid" in html
    assert ".approval-decision-form button { width: 100%" in html


def test_approvals_inbox_has_links_to_other_pages(monkeypatch, tmp_path):
    """/approvals/inbox must link to Monitoring, Simulation Dashboard, ERP Work Queue."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/approvals/inbox").text
    assert "/monitoring/live" in html
    assert "/simulation/dashboard" in html
    assert "/erp/work-queue" in html


# ---------------------------------------------------------------------------
# /monitoring/live legacy shell
# ---------------------------------------------------------------------------

def test_monitoring_live_has_live_monitoring_title(monkeypatch, tmp_path):
    """/monitoring/live must show 'Live Monitoring' as the page title."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/monitoring/live").text
    assert "Live Monitoring" in html


def test_monitoring_live_has_legacy_shell_elements(monkeypatch, tmp_path):
    """/monitoring/live must have Contoso title, Module Menu, breadcrumb."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/monitoring/live").text
    assert "Contoso Legacy ERP 2009" in html
    assert "Module Menu" in html
    assert "breadcrumb-bar" in html
    assert "legacy-footer" in html


def test_monitoring_live_has_live_data_fetch_script(monkeypatch, tmp_path):
    """/monitoring/live must have the JS fetch('/monitoring/live-data') polling script."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/monitoring/live").text
    assert "fetch('/monitoring/live-data'" in html
    assert "setInterval" in html
    assert "POLL_INTERVAL_MS" in html


def test_monitoring_live_preserves_dom_ids(monkeypatch, tmp_path):
    """/monitoring/live must preserve all stable DOM ids for JS panel updates."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/monitoring/live").text
    for dom_id in [
        "robot-status-panel",
        "approval-summary-panel",
        "queue-summary-panel",
        "injection-panel",
        "injection-result",
        "latest-cases-panel",
        "real-run-memory-panel",
        "proposal-inbox-panel",
        "audit-log-panel",
        "heartbeat-history-panel",
        "last-updated",
        "polling-indicator",
        "refresh-error",
    ]:
        assert f"id='{dom_id}'" in html, f"Missing DOM id: {dom_id}"


def test_monitoring_live_has_injection_buttons(monkeypatch, tmp_path):
    """/monitoring/live must keep the original scenarios and add demo seed sets."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/monitoring/live").text
    for scenario in ["normal", "budget_exceeded", "vendor_info_missing",
                     "inventory_shortage", "ambiguous",
                     "agent_context_review", "capex_budget_exception"]:
        assert f"data-scenario='{scenario}'" in html, f"Missing injection scenario: {scenario}"
    assert "API Proposal Seed Set" in html
    assert "XAML Workflow Proposal Seed Set" in html


def test_monitoring_live_has_injection_intercept_script(monkeypatch, tmp_path):
    """/monitoring/live must have JS intercept for injection forms."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/monitoring/live").text
    assert "setupInjectionIntercept" in html
    assert "preventDefault" in html
    assert "inject-form" in html


# ---------------------------------------------------------------------------
# /simulation/dashboard legacy shell
# ---------------------------------------------------------------------------

def test_simulation_dashboard_has_simulation_title(monkeypatch, tmp_path):
    """/simulation/dashboard must show 'Simulation Dashboard' as the page title."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    html = client.get("/simulation/dashboard").text
    assert "Simulation Dashboard" in html


def test_simulation_dashboard_has_legacy_shell_elements(monkeypatch, tmp_path):
    """/simulation/dashboard must have Contoso title, Module Menu, breadcrumb."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    html = client.get("/simulation/dashboard").text
    assert "Contoso Legacy ERP 2009" in html
    assert "Module Menu" in html
    assert "breadcrumb-bar" in html
    assert "legacy-footer" in html


# ---------------------------------------------------------------------------
# /demo/replay interactive product replay
# ---------------------------------------------------------------------------

def test_demo_replay_returns_interactive_page(monkeypatch, tmp_path):
    """/demo/replay renders the interactive UiPath + Agent replay page."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.get("/demo/replay")
    assert resp.status_code == 200
    html = resp.text
    assert "UiPath + Agentic ERP Modernization Replay" in html
    assert "Play Replay" in html
    assert "Next Step" in html
    assert "replaySteps" in html


def test_demo_replay_explains_evidence_model(monkeypatch, tmp_path):
    """/demo/replay distinguishes online replay, video proof, and GitHub evidence."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/demo/replay").text
    assert "Online Replay" in html
    assert "Demo Video" in html
    assert "GitHub" in html
    assert "real UiPath Robot execution proof" in html


def test_demo_replay_covers_governed_agent_flow(monkeypatch, tmp_path):
    """/demo/replay covers UiPath orchestration, agent decision, human approval, and Codex handoff."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/demo/replay").text
    assert "UiPath RPA" in html
    assert "LLM-backed Agent" in html
    assert "Enterprise Context" in html
    assert "Human Approval" in html
    assert "Proposal Pipeline" in html
    assert "Codex Handoff" in html
    assert "never calls Codex automatically" in html


# ---------------------------------------------------------------------------
# /proposals/inbox legacy shell + JSON compatibility
# ---------------------------------------------------------------------------

def test_proposals_inbox_default_returns_html(monkeypatch, tmp_path):
    """/proposals/inbox default must return text/html (legacy shell page)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.get("/proposals/inbox")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_proposals_inbox_html_has_legacy_shell(monkeypatch, tmp_path):
    """/proposals/inbox HTML must have Contoso title, Module Menu, Proposal Inbox title."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/proposals/inbox").text
    assert "Contoso Legacy ERP 2009" in html
    assert "Module Menu" in html
    assert "Proposal Inbox" in html
    assert "breadcrumb-bar" in html
    assert "legacy-footer" in html


def test_proposals_inbox_html_shows_empty_message(monkeypatch, tmp_path):
    """/proposals/inbox HTML with no proposals must show 'No real proposals yet.'"""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/proposals/inbox").text
    assert "No real proposals yet." in html
    assert "To create one, run repeated real UiPath cases" in html


def test_proposals_inbox_format_json_returns_json(monkeypatch, tmp_path):
    """/proposals/inbox?format=json must return JSON data."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.get("/proposals/inbox?format=json")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "proposals" in data
    assert isinstance(data["proposals"], list)


def test_proposals_inbox_json_has_correct_structure(monkeypatch, tmp_path):
    """/proposals/inbox?format=json must return total + proposals list."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    data = client.get("/proposals/inbox?format=json").json()
    assert data["total"] == 0
    assert len(data["proposals"]) == 0


# ---------------------------------------------------------------------------
# /erp/work-queue selectors preserved
# ---------------------------------------------------------------------------

def test_erp_work_queue_preserves_selectors(monkeypatch, tmp_path):
    """/erp/work-queue must preserve all ctl00_MainContent_* selectors."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    html = client.get("/erp/work-queue").text
    assert "ctl00_MainContent_btnOpenFirstPending" in html
    assert "ctl00_MainContent_grdPoWorkQueue" in html
    assert "ctl00_MainContent_grdPoWorkQueue_ctl02_btnOpen" in html


def test_erp_work_queue_empty_preserves_message_selector(monkeypatch, tmp_path):
    """Empty /erp/work-queue must preserve ctl00_MainContent_lblQueueEmptyMessage."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    html = client.get("/erp/work-queue").text
    assert "ctl00_MainContent_lblQueueEmptyMessage" in html


def test_erp_detail_page_preserves_14_label_selectors(monkeypatch, tmp_path):
    """Detail page must preserve all 14 ctl00_MainContent_lbl* selectors."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    html = client.get("/erp/work-queue/SIM-001").text
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
    ]:
        assert dom_id in html, f"Missing selector: {dom_id}"


def test_erp_detail_page_preserves_5_button_selectors(monkeypatch, tmp_path):
    """Detail page must preserve all 5 ctl00_MainContent_btn* action button selectors."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    html = client.get("/erp/work-queue/SIM-001").text
    for dom_id in [
        "ctl00_MainContent_btnMarkStandardProcessed",
        "ctl00_MainContent_btnMarkWaitingVendor",
        "ctl00_MainContent_btnFlagCapabilityGap",
        "ctl00_MainContent_btnSendManualInvestigation",
        "ctl00_MainContent_btnSubmitApprovalRequest",
    ]:
        assert dom_id in html, f"Missing button selector: {dom_id}"


def test_erp_detail_page_uses_legacy_shell(monkeypatch, tmp_path):
    """Detail page must also use the unified legacy shell."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    html = client.get("/erp/work-queue/SIM-001").text
    assert "Contoso Legacy ERP 2009" in html
    assert "Module Menu" in html
    assert "erp-shell" in html
    assert "erp-body" in html
    assert "module-menu" in html
    assert "erp-content-wrap" in html


# ---------------------------------------------------------------------------
# Module menu content
# ---------------------------------------------------------------------------

def test_module_menu_has_all_sections(monkeypatch, tmp_path):
    """Module menu must have all 4 sections: Procurement, Monitoring, Modernization, Simulation."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    html = client.get("/erp/work-queue").text
    assert "Procurement" in html
    assert "Monitoring" in html
    assert "Modernization" in html
    assert "Simulation" in html


def test_module_menu_has_procurement_items(monkeypatch, tmp_path):
    """Module menu must have Procurement items: Work Queue, Exceptions, Approval Requests."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    html = client.get("/erp/work-queue").text
    assert "Purchase Order Work Queue" in html
    assert "Purchase Order Exceptions" in html
    assert "Approval Requests" in html
    assert "Vendor Master Review" in html


def test_module_menu_has_modernization_items(monkeypatch, tmp_path):
    """Module menu must have Modernization items: Proposal Inbox, Evidence Snapshot, Case Portfolio."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    _setup_queue(client)
    html = client.get("/erp/work-queue").text
    assert "Proposal Inbox" in html
    assert "Evidence Snapshot" in html
    assert "Case Portfolio" in html
