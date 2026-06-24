from __future__ import annotations

APPROVAL_SIDE_EFFECTS = [
    "PO_STATUS_UPDATED",
    "APPROVAL_TASK_CREATED",
    "AUDIT_LOG_CREATED",
    "MANAGER_NOTIFICATION_QUEUED",
    "BUDGET_REVIEW_FLAGGED",
]

CLONED_INITIAL_STATE = {
    "amount": 18000,
    "budget_limit": 10000,
    "vendor_id": "V-203",
    "vendor_info_complete": True,
    "inventory_available": True,
    "status": "Exception",
    "raw_exception_text": "Amount exceeds approved budget limit",
    "exception": "Amount exceeds budget",
}


def run_parity_check(*, simulate_failure: bool = False) -> dict[str, object]:
    same_initial_state = True
    rpa_result = {
        "status": "PENDING_MANAGER_APPROVAL",
        "audit_log_created": True,
    }
    api_result = {
        "status": "PENDING_MANAGER_APPROVAL",
        "audit_log_created": not simulate_failure,
    }
    matched = list(APPROVAL_SIDE_EFFECTS)
    missing: list[str] = []
    if simulate_failure:
        matched.remove("AUDIT_LOG_CREATED")
        missing = ["AUDIT_LOG_CREATED"]

    passed = same_initial_state and rpa_result == api_result
    return {
        "data_isolation": "cloned_test_cases",
        "rpa_test_case_id": "PO-1001-RPA",
        "api_test_case_id": "PO-1001-API",
        "same_initial_state": same_initial_state,
        "rpa_result": rpa_result,
        "api_result": api_result,
        "rpa_api_parity_check": "passed" if passed else "failed",
        "compared_fields": ["status", "audit_log_created"],
        "matched_side_effects": matched,
        "missing_side_effects": missing,
        "extra_side_effects": [],
        "parity_summary": (
            "Hard MVP demo heuristic: cloned PO-1001-RPA and PO-1001-API start "
            "from identical seed data, then compare status and audit-log side effects."
        ),
    }
