"""Tests for Web-based Human Approval Inbox + Audit Trail.

Covers:
  - POST /approvals/create — create PENDING approval task
  - GET /approvals/inbox — HTML page with Approve/Reject forms
  - GET /approvals/{approval_id} — JSON task details
  - POST /approvals/{approval_id}/approve — approve with approver/comment
  - POST /approvals/{approval_id}/reject — reject with approver/comment
  - Run Memory event append on approve/reject
  - /monitoring/live shows pending approval count
  - /demo/evidence-snapshot includes approvals_summary
  - Safety: no Codex, no XAML, no API deploy, no trusted capability
"""
from __future__ import annotations

import json
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


def _create_run_for_case(client: TestClient, case_id: str, po_id: str) -> str:
    """Start a run and return the run_id."""
    start = client.post(
        "/memory/runs/start",
        json={"case_id": case_id, "po_id": po_id},
    ).json()
    return start["run_id"]


# ---------------------------------------------------------------------------
# POST /approvals/create
# ---------------------------------------------------------------------------

def test_create_approval_returns_pending(monkeypatch, tmp_path):
    """create approval returns PENDING status and approval_url."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    resp = client.post("/approvals/create", json={
        "case_id": "CASE-SIM-003",
        "po_id": "PO-SIM-003",
        "run_id": "RUN-TEST-001",
        "simulation_case_id": "SIM-003",
        "amount": 15000,
        "budget_limit": 10000,
        "reason": "Budget exceeded requires business approval.",
        "policy_decision": "REQUIRE_HUMAN_APPROVAL",
        "requested_by": "UIPATH-LOCAL-001",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["approval_id"].startswith("APR-")
    assert body["approval_url"] == f"/approvals/{body['approval_id']}"
    assert body["inbox_url"] == "/approvals/inbox"
    assert body["created_at"] is not None


# ---------------------------------------------------------------------------
# GET /approvals/{approval_id}
# ---------------------------------------------------------------------------

def test_get_approval_returns_full_task(monkeypatch, tmp_path):
    """GET /approvals/{id} returns the full task with audit_trail."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
        "po_id": "PO-1001",
        "amount": 18000,
        "budget_limit": 10000,
        "reason": "Budget exceeded",
    }).json()

    resp = client.get(f"/approvals/{create['approval_id']}")
    assert resp.status_code == 200
    task = resp.json()
    assert task["approval_id"] == create["approval_id"]
    assert task["status"] == "PENDING"
    assert task["case_id"] == "CASE-001"
    assert task["amount"] == 18000
    assert task["budget_limit"] == 10000
    assert len(task["audit_trail"]) == 1
    assert task["audit_trail"][0]["action"] == "APPROVAL_CREATED"


def test_approval_task_contains_business_remarks_and_agent_reasoning(monkeypatch, tmp_path):
    """Approval task stores the enterprise context evidence shown to approvers."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
        "po_id": "PO-1001",
        "amount": 18000,
        "budget_limit": 10000,
        "raw_exception_text": "Amount exceeds approved budget limit",
        "business_remarks": "Q4 customer delivery is at risk.",
        "agent_reasoning_summary": "Agent used finance and sales context.",
        "company_context_reference": {
            "finance_policy_used": True,
            "sales_context_used": True,
            "operations_context_used": True,
        },
        "policy_gate_reason": "Budget exception requires manager approval.",
        "agent_recommendation": "WAITING_FOR_HUMAN_APPROVAL",
        "reason": "Budget exceeded",
    }).json()

    task = client.get(f"/approvals/{create['approval_id']}").json()
    assert task["business_remarks"] == "Q4 customer delivery is at risk."
    assert task["agent_reasoning_summary"] == "Agent used finance and sales context."
    assert task["company_context_reference"]["finance_policy_used"] is True

    inbox = client.get("/approvals/inbox").text
    assert "Q4 customer delivery is at risk." in inbox
    assert "Agent used finance and sales context." in inbox
    assert "Company Context Snapshot" in inbox
    assert "Budget exception requires manager approval." in inbox


def test_get_approval_404_for_unknown(monkeypatch, tmp_path):
    """Unknown approval_id returns 404."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.get("/approvals/APR-FAKE-9999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /approvals/inbox
# ---------------------------------------------------------------------------

def test_inbox_shows_pending_task(monkeypatch, tmp_path):
    """Inbox HTML shows pending task with Approve/Reject forms."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    client.post("/approvals/create", json={
        "case_id": "CASE-SIM-003",
        "po_id": "PO-SIM-003",
        "run_id": "RUN-001",
        "amount": 15000,
        "budget_limit": 10000,
        "reason": "Budget exceeded",
    })

    resp = client.get("/approvals/inbox")
    assert resp.status_code == 200
    html = resp.text
    assert "Human Approval Inbox" in html
    assert "PENDING" in html
    assert "CASE-SIM-003" in html
    assert "Approve" in html
    assert "Reject" in html
    # Form actions.
    assert "/approve" in html
    assert "/reject" in html


def test_inbox_pending_forms_use_consistent_layout_classes(monkeypatch, tmp_path):
    """Approve/Reject manual-entry forms should render as aligned form blocks."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    create = client.post("/approvals/create", json={
        "case_id": "CASE-SIM-003",
        "po_id": "PO-SIM-003",
        "reason": "Budget exceeded",
    }).json()

    html = client.get("/approvals/inbox").text
    assert "approval-actions" in html
    assert html.count("class='approval-decision-form'") == 2
    assert f"action='/approvals/{create['approval_id']}/approve'" in html
    assert f"action='/approvals/{create['approval_id']}/reject'" in html
    assert "style='display:inline;'" not in html


def test_inbox_empty(monkeypatch, tmp_path):
    """Inbox with no tasks shows empty message."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.get("/approvals/inbox")
    assert resp.status_code == 200
    assert "No approval tasks" in resp.text


# ---------------------------------------------------------------------------
# POST /approvals/{approval_id}/approve
# ---------------------------------------------------------------------------

def test_approve_records_approver_comment(monkeypatch, tmp_path):
    """Approve sets status=APPROVED_PENDING_ERP_WRITEBACK, records approver/comment/approved_at."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
        "po_id": "PO-1001",
        "reason": "Budget exceeded",
    }).json()
    approval_id = create["approval_id"]

    # Approve via JSON.
    resp = client.post(f"/approvals/{approval_id}/approve", json={
        "approver": "manager@example.com",
        "comment": "Approved with conditions.",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "APPROVED_PENDING_ERP_WRITEBACK"
    assert body["decision"] == "APPROVED"
    assert body["approver"] == "manager@example.com"
    assert body["comment"] == "Approved with conditions."
    assert body["approved_at"] is not None
    # Safety flags.
    assert body["codex_called"] is False
    assert body["xaml_modified"] is False
    assert body["api_deployed"] is False

    # Verify the task is persisted.
    task = client.get(f"/approvals/{approval_id}").json()
    assert task["status"] == "APPROVED_PENDING_ERP_WRITEBACK"
    assert task["approver"] == "manager@example.com"
    # Audit trail has 2 entries: CREATED + APPROVED.
    assert len(task["audit_trail"]) == 2
    assert task["audit_trail"][1]["action"] == "APPROVAL_APPROVED"


def test_approve_via_form(monkeypatch, tmp_path):
    """Approve via form-encoded data redirects back to the HTML inbox."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
        "reason": "Budget exceeded",
    }).json()

    resp = client.post(f"/approvals/{create['approval_id']}/approve", data={
        "approver": "admin",
        "comment": "Form-based approval",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/approvals/inbox?")
    assert "approval_result=approved" in resp.headers["location"]
    assert f"approval_id={create['approval_id']}" in resp.headers["location"]

    task = client.get(f"/approvals/{create['approval_id']}").json()
    assert task["status"] == "APPROVED_PENDING_ERP_WRITEBACK"
    assert task["approver"] == "admin"


def test_approve_via_form_follow_redirect_shows_updated_inbox(monkeypatch, tmp_path):
    """Browser form submissions land on the inbox instead of a JSON page."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
        "reason": "Budget exceeded",
    }).json()

    resp = client.post(f"/approvals/{create['approval_id']}/approve", data={
        "approver": "admin",
        "comment": "Form-based approval",
    })
    assert resp.status_code == 200
    assert "Human Approval Inbox" in resp.text
    assert "was approved" in resp.text
    assert "APPROVED_PENDING_ERP_WRITEBACK" in resp.text
    assert "\"approval_id\"" not in resp.text


def test_approve_requires_approver(monkeypatch, tmp_path):
    """Approve without approver returns 400."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
    }).json()

    resp = client.post(f"/approvals/{create['approval_id']}/approve", json={
        "comment": "No approver",
    })
    assert resp.status_code == 400


def test_approve_already_decided_400(monkeypatch, tmp_path):
    """Cannot approve an already-decided task."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
    }).json()

    # First approve.
    client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "mgr",
        "comment": "ok",
    })

    # Second approve should 400.
    resp = client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "mgr2",
        "comment": "again",
    })
    assert resp.status_code == 400


def test_approve_404_for_unknown(monkeypatch, tmp_path):
    """Approve unknown approval_id returns 404."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    resp = client.post("/approvals/APR-FAKE-9999/approve", json={
        "approver": "mgr",
        "comment": "ok",
    })
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /approvals/{approval_id}/reject
# ---------------------------------------------------------------------------

def test_reject_records_approver_comment(monkeypatch, tmp_path):
    """Reject sets status=REJECTED, records approver/comment/approved_at."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
        "reason": "Budget exceeded",
    }).json()

    resp = client.post(f"/approvals/{create['approval_id']}/reject", json={
        "approver": "cfo@example.com",
        "comment": "Amount too high, need budget revision.",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert body["decision"] == "REJECTED"
    assert body["approver"] == "cfo@example.com"
    assert body["approved_at"] is not None

    # Verify audit trail.
    task = client.get(f"/approvals/{create['approval_id']}").json()
    assert task["audit_trail"][-1]["action"] == "APPROVAL_REJECTED"


def test_reject_via_form_follow_redirect_shows_updated_inbox(monkeypatch, tmp_path):
    """Reject via form-encoded data lands back on the HTML inbox."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
        "reason": "Budget exceeded",
    }).json()

    resp = client.post(f"/approvals/{create['approval_id']}/reject", data={
        "approver": "cfo",
        "comment": "Insufficient budget.",
    })
    assert resp.status_code == 200
    assert "Human Approval Inbox" in resp.text
    assert "was rejected" in resp.text
    assert "REJECTED" in resp.text

    task = client.get(f"/approvals/{create['approval_id']}").json()
    assert task["status"] == "REJECTED"
    assert task["approver"] == "cfo"


# ---------------------------------------------------------------------------
# Run Memory event append
# ---------------------------------------------------------------------------

def test_approve_appends_run_memory_event(monkeypatch, tmp_path):
    """Approve appends HUMAN_APPROVAL_COMPLETED event to Run Memory."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    # Create a real run.
    run_id = _create_run_for_case(client, "CASE-001", "PO-1001")

    # Create approval linked to the run.
    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
        "po_id": "PO-1001",
        "run_id": run_id,
        "amount": 18000,
        "budget_limit": 10000,
        "reason": "Budget exceeded",
    }).json()

    # Approve.
    resp = client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "mgr",
        "comment": "ok",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_memory_event"]["appended"] is True
    assert body["run_memory_event"]["event_type"] == "HUMAN_APPROVAL_COMPLETED"

    # Verify the event is in the raw events file.
    events_path = tmp_path / "runs" / run_id / "raw" / "uipath_execution_events.jsonl"
    events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
    approval_events = [e for e in events if e["event_type"] == "HUMAN_APPROVAL_COMPLETED"]
    assert len(approval_events) == 1
    assert approval_events[0]["payload"]["approver"] == "mgr"
    assert approval_events[0]["payload"]["decision"] == "APPROVED"


def test_reject_appends_run_memory_event(monkeypatch, tmp_path):
    """Reject appends HUMAN_APPROVAL_REJECTED event to Run Memory."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    run_id = _create_run_for_case(client, "CASE-002", "PO-1002")

    create = client.post("/approvals/create", json={
        "case_id": "CASE-002",
        "po_id": "PO-1002",
        "run_id": run_id,
        "reason": "Budget exceeded",
    }).json()

    resp = client.post(f"/approvals/{create['approval_id']}/reject", json={
        "approver": "cfo",
        "comment": "Rejected",
    })
    assert resp.status_code == 200
    assert resp.json()["run_memory_event"]["appended"] is True
    assert resp.json()["run_memory_event"]["event_type"] == "HUMAN_APPROVAL_REJECTED"

    # Verify event in Run Memory.
    events_path = tmp_path / "runs" / run_id / "raw" / "uipath_execution_events.jsonl"
    events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
    reject_events = [e for e in events if e["event_type"] == "HUMAN_APPROVAL_REJECTED"]
    assert len(reject_events) == 1


def test_approve_without_run_id_does_not_fail(monkeypatch, tmp_path):
    """Approve without run_id should not fail, just skip Run Memory event."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
        # No run_id
    }).json()

    resp = client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "mgr",
        "comment": "ok",
    })
    assert resp.status_code == 200
    assert resp.json()["run_memory_event"]["appended"] is False
    assert "no run_id" in resp.json()["run_memory_event"]["reason"]


# ---------------------------------------------------------------------------
# Monitoring + Evidence Snapshot integration
# ---------------------------------------------------------------------------

def test_monitoring_shows_pending_approval_count(monkeypatch, tmp_path):
    """/monitoring/live shows pending approval count."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    # No approvals yet.
    resp = client.get("/monitoring/live")
    assert "Human Approval Inbox" in resp.text
    assert "No pending approvals" in resp.text

    # Create 2 pending approvals.
    client.post("/approvals/create", json={"case_id": "CASE-001"})
    client.post("/approvals/create", json={"case_id": "CASE-002"})

    resp = client.get("/monitoring/live")
    assert "2" in resp.text  # pending count = 2
    assert "/approvals/inbox" in resp.text


def test_evidence_snapshot_includes_approvals_summary(monkeypatch, tmp_path):
    """/demo/evidence-snapshot includes approvals_summary."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    client.post("/approvals/create", json={"case_id": "CASE-001", "amount": 18000})

    snap = client.get("/demo/evidence-snapshot").json()
    assert "approvals_summary" in snap
    summary = snap["approvals_summary"]
    assert summary["pending"] == 1
    assert summary["total"] == 1
    assert len(summary["recent"]) == 1
    assert summary["recent"][0]["case_id"] == "CASE-001"


def test_simulation_dashboard_has_approval_link(monkeypatch, tmp_path):
    """Simulation dashboard has link to /approvals/inbox."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    resp = client.get("/simulation/dashboard")
    assert "/approvals/inbox" in resp.text


# ---------------------------------------------------------------------------
# Inbox shows decided tasks
# ---------------------------------------------------------------------------

def test_inbox_shows_decided_task_without_forms(monkeypatch, tmp_path):
    """After approval, inbox shows the task but without Approve/Reject forms."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-001",
        "reason": "Budget exceeded",
    }).json()

    client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "mgr",
        "comment": "ok",
    })

    resp = client.get("/approvals/inbox")
    html = resp.text
    assert "APPROVED_PENDING_ERP_WRITEBACK" in html
    assert "mgr" in html
    # The approved task should not have forms.
    # (PENDING tasks have forms; decided tasks should not.)
    # Count forms — should be 0 for the approved task.
    assert html.count("action='/approvals/") == 0


# ---------------------------------------------------------------------------
# Multiple approvals
# ---------------------------------------------------------------------------

def test_multiple_approvals_lifecycle(monkeypatch, tmp_path):
    """Create 3 approvals, approve 2, reject 1, verify counts."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    ids = []
    for i in range(3):
        create = client.post("/approvals/create", json={
            "case_id": f"CASE-{i:03d}",
            "reason": f"Approval {i}",
        }).json()
        ids.append(create["approval_id"])

    # Approve first 2.
    client.post(f"/approvals/{ids[0]}/approve", json={"approver": "mgr1", "comment": "ok1"})
    client.post(f"/approvals/{ids[1]}/approve", json={"approver": "mgr2", "comment": "ok2"})
    # Reject third.
    client.post(f"/approvals/{ids[2]}/reject", json={"approver": "mgr3", "comment": "no"})

    # Evidence snapshot.
    snap = client.get("/demo/evidence-snapshot").json()
    summary = snap["approvals_summary"]
    assert summary["pending"] == 0
    assert summary["approved"] == 2
    assert summary["rejected"] == 1
    assert summary["total"] == 3


# ---------------------------------------------------------------------------
# ERP Writeback tracking: approved-pending-writeback, mark-writeback-*
# ---------------------------------------------------------------------------

def test_approve_sets_approved_pending_erp_writeback(monkeypatch, tmp_path):
    """Approve must set status=APPROVED_PENDING_ERP_WRITEBACK (not just APPROVED)."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-WB-001",
        "simulation_case_id": "SIM-WB-001",
        "reason": "Budget exceeded",
    }).json()

    resp = client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "cfo",
        "comment": "Approved pending ERP writeback",
    })
    assert resp.json()["status"] == "APPROVED_PENDING_ERP_WRITEBACK"
    assert resp.json()["decision"] == "APPROVED"


def test_approved_pending_writeback_lists_tasks(monkeypatch, tmp_path):
    """approved-pending-writeback returns tasks awaiting ERP writeback."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    # Create and approve 2 tasks.
    for i in range(2):
        create = client.post("/approvals/create", json={
            "case_id": f"CASE-WB-{i:03d}",
            "po_id": f"PO-WB-{i:03d}",
            "simulation_case_id": f"SIM-WB-{i:03d}",
            "run_id": f"RUN-WB-{i:03d}",
            "reason": "Budget exceeded",
        }).json()
        client.post(f"/approvals/{create['approval_id']}/approve", json={
            "approver": "mgr",
            "comment": "ok",
        })

    resp = client.get("/approvals/approved-pending-writeback")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    item = body["items"][0]
    assert "approval_id" in item
    assert "simulation_case_id" in item
    assert "case_id" in item
    assert "po_id" in item
    assert "run_id" in item
    assert "approval_url" in item
    assert "erp_detail_url" in item
    assert "status" in item
    assert item["status"] == "APPROVED_PENDING_ERP_WRITEBACK"
    assert item["erp_detail_url"].startswith("/erp/work-queue/")


def test_mark_writeback_started_transitions_status(monkeypatch, tmp_path):
    """mark-writeback-started transitions to ERP_WRITEBACK_IN_PROGRESS."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-WB-001",
    }).json()
    client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "mgr",
        "comment": "ok",
    })

    resp = client.post(f"/approvals/{create['approval_id']}/mark-writeback-started", json={
        "robot_id": "UIPATH-LOCAL-001",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ERP_WRITEBACK_IN_PROGRESS"
    assert body["robot_id"] == "UIPATH-LOCAL-001"
    assert body["codex_called"] is False

    # Verify audit trail.
    task = client.get(f"/approvals/{create['approval_id']}").json()
    actions = [a["action"] for a in task["audit_trail"]]
    assert "ERP_WRITEBACK_IN_PROGRESS" in actions


def test_mark_writeback_completed_appends_run_memory_event(monkeypatch, tmp_path):
    """mark-writeback-completed appends ERP_WRITEBACK_COMPLETED to Run Memory."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    # Create a real run so Run Memory event can be appended.
    run_id = _create_run_for_case(client, "CASE-WB-001", "PO-WB-001")

    create = client.post("/approvals/create", json={
        "case_id": "CASE-WB-001",
        "po_id": "PO-WB-001",
        "run_id": run_id,
        "simulation_case_id": "SIM-WB-001",
    }).json()
    client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "mgr",
        "comment": "ok",
    })

    resp = client.post(f"/approvals/{create['approval_id']}/mark-writeback-completed", json={
        "erp_action": "SUBMIT_APPROVAL_REQUEST_CLICKED",
        "robot_id": "UIPATH-LOCAL-001",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ERP_WRITEBACK_COMPLETED"
    assert body["erp_action"] == "SUBMIT_APPROVAL_REQUEST_CLICKED"
    assert body["run_memory_event"]["appended"] is True
    assert body["run_memory_event"]["event_type"] == "ERP_WRITEBACK_COMPLETED"
    assert body["codex_called"] is False

    # Verify event in Run Memory JSONL.
    events_path = tmp_path / "runs" / run_id / "raw" / "uipath_execution_events.jsonl"
    events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
    writeback_events = [e for e in events if e["event_type"] == "ERP_WRITEBACK_COMPLETED"]
    assert len(writeback_events) == 1
    assert writeback_events[0]["payload"]["erp_action"] == "SUBMIT_APPROVAL_REQUEST_CLICKED"
    assert writeback_events[0]["payload"]["robot_id"] == "UIPATH-LOCAL-001"


def test_mark_writeback_completed_without_run_id_does_not_fail(monkeypatch, tmp_path):
    """mark-writeback-completed without run_id should not fail."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-WB-002",
    }).json()
    client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "mgr",
        "comment": "ok",
    })

    resp = client.post(f"/approvals/{create['approval_id']}/mark-writeback-completed", json={
        "erp_action": "MARK_STANDARD_PROCESSED_CLICKED",
        "robot_id": "UIPATH-LOCAL-001",
    })
    assert resp.status_code == 200
    assert resp.json()["run_memory_event"]["appended"] is False


def test_mark_writeback_completed_syncs_simulation_case(monkeypatch, tmp_path):
    """mark-writeback-completed should update the linked simulation case erp_status/last_action."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    client.post("/simulation/reset")

    # Link approval to an existing simulation case SIM-001.
    create = client.post("/approvals/create", json={
        "case_id": "CASE-WB-SIM-001",
        "po_id": "PO-SIM-001",
        "simulation_case_id": "SIM-001",
        "reason": "Budget exceeded",
    }).json()
    client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "mgr",
        "comment": "ok",
    })

    resp = client.post(f"/approvals/{create['approval_id']}/mark-writeback-completed", json={
        "erp_action": "SUBMIT_APPROVAL_REQUEST_CLICKED",
        "robot_id": "UIPATH-LOCAL-001",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ERP_WRITEBACK_COMPLETED"
    assert body["simulation_case_id"] == "SIM-001"
    assert body["simulation_case_updated"] is True

    # Verify the simulation case erp_status/last_action were updated.
    detail = client.get("/erp/work-queue/SIM-001").text
    assert "ERP_APPROVAL_REQUESTED" in detail
    assert "SUBMIT_APPROVAL_REQUEST" in detail


def test_mark_writeback_completed_without_simulation_case_id(monkeypatch, tmp_path):
    """mark-writeback-completed without simulation_case_id should not fail, sim_case_updated=False."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-WB-NOSIM",
        # No simulation_case_id
    }).json()
    client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "mgr",
        "comment": "ok",
    })

    resp = client.post(f"/approvals/{create['approval_id']}/mark-writeback-completed", json={
        "erp_action": "SUBMIT_APPROVAL_REQUEST_CLICKED",
        "robot_id": "UIPATH-LOCAL-001",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ERP_WRITEBACK_COMPLETED"
    assert body["simulation_case_updated"] is False


def test_mark_writeback_completed_400_for_wrong_status(monkeypatch, tmp_path):
    """mark-writeback-completed on a PENDING task should return 400."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    create = client.post("/approvals/create", json={
        "case_id": "CASE-WB-003",
    }).json()

    resp = client.post(f"/approvals/{create['approval_id']}/mark-writeback-completed", json={
        "erp_action": "test",
        "robot_id": "robot",
    })
    assert resp.status_code == 400


def test_mark_writeback_404_for_unknown(monkeypatch, tmp_path):
    """mark-writeback-* on unknown approval_id returns 404."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))

    resp = client.post("/approvals/APR-FAKE-9999/mark-writeback-started", json={})
    assert resp.status_code == 404

    resp = client.post("/approvals/APR-FAKE-9999/mark-writeback-completed", json={})
    assert resp.status_code == 404


def test_full_approval_to_writeback_lifecycle(monkeypatch, tmp_path):
    """Full lifecycle: create → approve → pending-writeback → started → completed."""
    client = TestClient(load_app(monkeypatch, run_memory_root=tmp_path))
    run_id = _create_run_for_case(client, "CASE-FULL-001", "PO-FULL-001")

    # 1. Create.
    create = client.post("/approvals/create", json={
        "case_id": "CASE-FULL-001",
        "po_id": "PO-FULL-001",
        "run_id": run_id,
        "simulation_case_id": "SIM-FULL-001",
    }).json()
    assert create["status"] == "PENDING"

    # 2. Approve → APPROVED_PENDING_ERP_WRITEBACK.
    resp = client.post(f"/approvals/{create['approval_id']}/approve", json={
        "approver": "cfo",
        "comment": "Approved",
    })
    assert resp.json()["status"] == "APPROVED_PENDING_ERP_WRITEBACK"

    # 3. Pending writeback list shows it.
    pending = client.get("/approvals/approved-pending-writeback").json()
    assert len(pending["items"]) == 1

    # 4. Mark started.
    resp = client.post(f"/approvals/{create['approval_id']}/mark-writeback-started", json={
        "robot_id": "UIPATH-LOCAL-001",
    })
    assert resp.json()["status"] == "ERP_WRITEBACK_IN_PROGRESS"

    # 5. After started, pending-writeback should no longer list it.
    pending = client.get("/approvals/approved-pending-writeback").json()
    assert len(pending["items"]) == 0

    # 6. Mark completed.
    resp = client.post(f"/approvals/{create['approval_id']}/mark-writeback-completed", json={
        "erp_action": "SUBMIT_APPROVAL_REQUEST_CLICKED",
        "robot_id": "UIPATH-LOCAL-001",
    })
    assert resp.json()["status"] == "ERP_WRITEBACK_COMPLETED"
    assert resp.json()["run_memory_event"]["appended"] is True

    # 7. Verify audit trail has all stages.
    task = client.get(f"/approvals/{create['approval_id']}").json()
    actions = [a["action"] for a in task["audit_trail"]]
    assert "APPROVAL_CREATED" in actions
    assert "APPROVAL_APPROVED" in actions
    assert "ERP_WRITEBACK_IN_PROGRESS" in actions
    assert "ERP_WRITEBACK_COMPLETED" in actions
