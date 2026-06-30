import sys
from pathlib import Path

from fastapi import HTTPException


def load_service(monkeypatch=None, memory_dir=None):
    service_root = Path(__file__).resolve().parents[1]
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name)
    if monkeypatch is not None and memory_dir is not None:
        monkeypatch.setenv("AUTOMATION_MEMORY_DIR", str(memory_dir))
    elif monkeypatch is not None:
        monkeypatch.delenv("AUTOMATION_MEMORY_DIR", raising=False)
    sys.path.insert(0, str(service_root))
    from app.db import audit_count, approval_task_count, init_db, purchase_order_status
    import app.main as main

    init_db()
    return main, audit_count, approval_task_count, purchase_order_status


def post_approval(main, po_id, body):
    payload = main.ApprovalRequest(**body)
    return main.request_purchase_order_approval(po_id, payload)


def test_purchase_order_approval_route_declares_202(monkeypatch, tmp_path):
    main, *_ = load_service(monkeypatch, tmp_path)
    route = next(
        route
        for route in main.app.routes
        if getattr(route, "path", "") == "/api/purchase-orders/{po_id}/approval-request"
    )

    assert route.status_code == 202
    assert route.response_model is main.ApprovalTaskResponse


def test_purchase_order_approval_returns_accepted_task(monkeypatch, tmp_path):
    main, audit_count, approval_task_count, purchase_order_status = load_service(
        monkeypatch,
        tmp_path,
    )
    response = post_approval(
        main,
        "PO-1001",
        {
            "approval_reason": "Amount exceeds budget limit",
            "manager_id": "MGR-001",
            "source_case_id": "CASE-001",
        },
    )

    body = response.model_dump()
    assert body == {
        "po_id": "PO-1001",
        "task_id": "TASK-PO-1001-APPROVAL",
        "status": "PENDING_HUMAN_APPROVAL",
        "business_action": "manual_investigation",
        "process_signature": (
            "manual_investigation__budget_exceeded__waiting_for_human_approval__"
            "require_human_approval__no_side_effects"
        ),
        "audit_log_created": True,
        "source_case_id": "CASE-001",
        "business_side_effects": [],
        "evidence_run_ids": [
            "RUN-20260630-001",
            "RUN-20260630-002",
            "RUN-20260630-003",
            "RUN-20260630-004",
        ],
        "observed_count": 4,
    }
    assert audit_count("PO-1001") == 1
    assert approval_task_count("PO-1001") == 1
    assert purchase_order_status("PO-1001") == "Exception"


def test_purchase_order_approval_is_idempotent(monkeypatch, tmp_path):
    main, audit_count, approval_task_count, _ = load_service(monkeypatch, tmp_path)
    body = {
        "approval_reason": "Vendor exception",
        "manager_id": "MGR-001",
        "source_case_id": "CASE-002",
    }

    first = post_approval(main, "PO-1002", body)
    first_count = audit_count("PO-1002")
    first_task_count = approval_task_count("PO-1002")
    response = post_approval(main, "PO-1002", body)
    second_count = audit_count("PO-1002")
    second_task_count = approval_task_count("PO-1002")

    assert response.task_id == first.task_id
    assert second_count == first_count
    assert first_count == 1
    assert second_task_count == first_task_count
    assert second_task_count == 1


def test_purchase_order_approval_writes_human_approval_memory(monkeypatch, tmp_path):
    main, *_ = load_service(monkeypatch, tmp_path)
    response = post_approval(
        main,
        "PO-1001-API",
        {
            "case_id": "CASE-API-001",
            "correlation_id": "corr-api-test",
            "approval_reason": "Amount exceeds budget limit",
            "manager_id": "MGR-001",
            "source_case_id": "CASE-001",
        },
    )

    body = response.model_dump()
    assert body["task_id"] == "TASK-PO-1001-API-APPROVAL"

    from shared.automation_memory.event_types import MemoryEventType
    from shared.automation_memory.repository import query_case_timeline

    events = query_case_timeline("CASE-API-001", data_dir=tmp_path)
    approval_events = [
        event
        for event in events
        if event.event_type == MemoryEventType.HUMAN_APPROVAL_COMPLETED
    ]
    assert len(approval_events) == 1
    event = approval_events[0]
    assert event.source_service == "generated-api-facade"
    assert event.correlation_id == "corr-api-test"
    assert event.payload["po_id"] == "PO-1001-API"
    assert event.payload["case_id"] == "CASE-API-001"
    assert event.payload["task_id"] == "TASK-PO-1001-API-APPROVAL"
    assert event.payload["business_action"] == "manual_investigation"
    assert event.payload["execution_mode"] == "HUMAN_APPROVAL"
    assert event.payload["status"] == "PENDING_HUMAN_APPROVAL"
    assert event.payload["audit_log_created"] is True
    assert event.payload["business_side_effects"] == []
    assert event.payload["observed_count"] == 4


def test_purchase_order_approval_memory_case_id_falls_back_to_source_case_id(
    monkeypatch,
    tmp_path,
):
    main, *_ = load_service(monkeypatch, tmp_path)
    response = post_approval(
        main,
        "PO-1001",
        {
            "approval_reason": "Amount exceeds budget limit",
            "manager_id": "MGR-001",
            "source_case_id": "CASE-SOURCE-001",
        },
    )

    assert response.task_id == "TASK-PO-1001-APPROVAL"
    from shared.automation_memory.repository import query_case_timeline

    events = query_case_timeline("CASE-SOURCE-001", data_dir=tmp_path)
    assert len(events) == 1
    assert events[0].payload["po_id"] == "PO-1001"
    assert events[0].payload["task_id"] == "TASK-PO-1001-APPROVAL"


def test_purchase_order_approval_memory_write_failure_does_not_block_response(
    monkeypatch,
    tmp_path,
):
    main, *_ = load_service(monkeypatch, tmp_path)

    def fail_record_human_approval(*_args, **_kwargs):
        raise RuntimeError("memory unavailable")

    monkeypatch.setattr(main, "record_human_approval", fail_record_human_approval)
    response = post_approval(
        main,
        "PO-1001",
        {
            "approval_reason": "Amount exceeds budget limit",
            "manager_id": "MGR-001",
            "source_case_id": "CASE-001",
        },
    )

    body = response.model_dump()
    assert body["po_id"] == "PO-1001"
    assert body["task_id"] == "TASK-PO-1001-APPROVAL"
    assert body["status"] == "PENDING_HUMAN_APPROVAL"


def test_unknown_purchase_order_returns_404(monkeypatch, tmp_path):
    main, *_ = load_service(monkeypatch, tmp_path)

    try:
        post_approval(
            main,
            "PO-MISSING",
            {
                "approval_reason": "Amount exceeds budget limit",
                "manager_id": "MGR-001",
                "source_case_id": "CASE-404",
            },
        )
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Purchase order not found"
    else:
        raise AssertionError("Expected HTTPException for missing purchase order")
