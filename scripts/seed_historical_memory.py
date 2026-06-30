#!/usr/bin/env python3
"""Seed Automation Memory with demo historical data.

Simulates an enterprise system that has run many cases over time, so the
capability-evolution demo can show realistic historical patterns. This is NOT
real production data — every seeded record carries ``source="demo_seeded_history"``.

Idempotent: re-running overwrites the memory/data JSON files with fresh seed
data. It does NOT touch the SQLite event store (memory-data/events.db) which is
the structured system of record; this script only seeds the demo-facing JSON
artifacts under ``memory/data/``.

Usage:
    .venv/bin/python scripts/seed_historical_memory.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from memory import repository as repo


DEMO_SOURCE = "demo_seeded_history"


# ---------------------------------------------------------------------------
# CASE-001 main-link artifacts (PO-1001 budget_exceeded -> API modernization)
# ---------------------------------------------------------------------------

def seed_case_001_artifacts() -> None:
    """Re-seed the complete CASE-001 main-link memory artifacts."""
    validation_result = {
        "capability_id": "request_purchase_order_approval_api",
        "case_id": "CASE-001",
        "business_action": "request_purchase_order_approval",
        "contract_test": "passed",
        "business_rule_test": "passed",
        "rpa_api_parity_check": "passed",
        "same_initial_state": True,
        "rpa_result": {
            "status": "PENDING_MANAGER_APPROVAL",
            "audit_log_created": True,
        },
        "api_result": {
            "status": "PENDING_MANAGER_APPROVAL",
            "audit_log_created": True,
        },
        "trusted_tool_candidate": True,
        "requires_registration_approval": True,
        "data_isolation": "cloned_test_cases",
        "rpa_test_case_id": "PO-1001-RPA",
        "api_test_case_id": "PO-1001-API",
        "compared_fields": ["status", "audit_log_created"],
        "matched_side_effects": [
            "PO_STATUS_UPDATED",
            "APPROVAL_TASK_CREATED",
            "AUDIT_LOG_CREATED",
            "MANAGER_NOTIFICATION_QUEUED",
            "BUDGET_REVIEW_FLAGGED",
        ],
        "missing_side_effects": [],
        "extra_side_effects": [],
        "parity_summary": (
            "Demo parity heuristic: cloned PO-1001-RPA and PO-1001-API start "
            "from identical seed data, then compare status and audit-log side "
            "effects."
        ),
    }

    repo.record_case_001_hard_mvp_artifacts(validation_result)

    # Modernization readiness for CASE-001.
    repo.record_modernization_readiness(
        "CASE-001",
        {
            "business_action": "request_purchase_order_approval",
            "modernization_candidate": True,
            "readiness_score": 0.88,
            "frequency_score": 0.86,
            "risk_score": 0.32,
            "recommended_api_tool_name": "request_purchase_order_approval_api",
            "recommended_next_stage": "GENERATE_PLAN",
            "reasoning_summary": (
                "High frequency and field stability with moderate UI fragility "
                "make this a strong API modernization candidate."
            ),
            "blocking_reasons": [],
            "side_effects_observed": True,
            "rpa_api_parity_required": True,
            "agent_runtime": "langgraph",
            "source": DEMO_SOURCE,
        },
    )

    # Modernization plan for CASE-001.
    repo.record_modernization_plan(
        "CASE-001",
        {
            "plan_id": "MOD-PLAN-001",
            "target_tool_name": "request_purchase_order_approval_api",
            "target_service": "generated-api-facade",
            "proposed_endpoint": "POST /api/purchase-orders/{po_id}/approval-request",
            "source_rpa_trace": "rpa_trace_CASE-001.json",
            "contract_requirements": [
                "Must ensure RPA/API side effects parity, including same notification and approval flow.",
                "API must return same status codes and error handling as RPA.",
                "Must adhere to security and authorization standards.",
            ],
            "tests_required": [
                "Test side effects parity between RPA and API.",
                "Test endpoint response time within SLA.",
                "Test authorization for different roles.",
            ],
            "side_effects_signature": [
                "PO_STATUS_UPDATED",
                "APPROVAL_TASK_CREATED",
                "AUDIT_LOG_CREATED",
                "MANAGER_NOTIFICATION_QUEUED",
                "BUDGET_REVIEW_FLAGGED",
            ],
            "rpa_api_parity_required": True,
            "risk_level": "medium",
            "requires_engineer_approval": True,
            "recommended_next_stage": "AWAITING_HUMAN_APPROVAL",
            "source": DEMO_SOURCE,
        },
    )


# ---------------------------------------------------------------------------
# Capability evolution decisions
# ---------------------------------------------------------------------------

def seed_capability_evolution_decisions() -> None:
    """Seed capability-evolution decisions for CASE-001 and CASE-003."""
    repo.record_capability_evolution_decision(
        "CASE-001",
        {
            "po_id": "PO-1001",
            "detected_exception_type": "budget_exceeded",
            "required_business_action": "request_purchase_order_approval",
            "trusted_capability_found": True,
            "trusted_capability_id": "request_purchase_order_approval_api",
            "recommended_evolution": "REUSE_TRUSTED_CAPABILITY",
            "decision_reason": (
                "A trusted API capability already covers this business action "
                "and has passed validation. Reuse it directly."
            ),
            "current_case_resolution": "api_mode_executed",
            "human_approval_required": False,
            "source": DEMO_SOURCE,
        },
    )

    repo.record_capability_evolution_decision(
        "CASE-003",
        {
            "po_id": "PO-1003",
            "detected_exception_type": "inventory_shortage",
            "required_business_action": "request_inventory_review",
            "trusted_capability_found": False,
            "repeated_gap_count": 9,
            "recommended_evolution": "XAML_WORKFLOW_PROPOSAL",
            "decision_reason": (
                "No trusted capability covers this action. Repeated gap count "
                "is high and field stability is moderate, so propose a new "
                "UiPath workflow rather than waiting."
            ),
            "proposed_workflow_name": "HandleInventoryShortageReview.xaml",
            "current_case_resolution": "manual_handling_required",
            "human_approval_required": True,
            "source": DEMO_SOURCE,
        },
    )


# ---------------------------------------------------------------------------
# Historical patterns (simulated enterprise history)
# ---------------------------------------------------------------------------

def seed_historical_patterns() -> None:
    """Seed historical_patterns.json with simulated long-run enterprise data."""
    patterns = [
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
            "source": DEMO_SOURCE,
        },
        {
            "exception_type": "inventory_shortage",
            "business_action": "request_inventory_review",
            "observed_count": 9,
            "repeated_gap_count": 9,
            "trusted_capability_found": False,
            "manual_handling_count": 9,
            "field_stability": 0.55,
            "ui_fragility": 0.72,
            "recommended_evolution": "XAML_WORKFLOW_PROPOSAL",
            "source": DEMO_SOURCE,
        },
        {
            "exception_type": "vendor_info_missing",
            "business_action": "handle_vendor_info_missing",
            "observed_count": 5,
            "recommended_evolution": "WAIT_FOR_VENDOR_INFO",
            "no_api_modernization_required": True,
            "trusted_capability_found": False,
            "source": DEMO_SOURCE,
        },
        {
            "exception_type": "selector_fragility",
            "business_action": "read_purchase_order",
            "observed_count": 4,
            "selector_failure_count": 3,
            "recommended_evolution": "XAML_IMPROVEMENT_PROPOSAL",
            "trusted_capability_found": True,
            "source": DEMO_SOURCE,
        },
    ]
    repo.record_historical_patterns(patterns)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

EXPECTED_FILES = [
    "case_state_CASE-001.json",
    "case_timeline_CASE-001.json",
    "agent_decision_CASE-001.json",
    "human_approval_CASE-001.json",
    "rpa_trace_CASE-001.json",
    "validation_result_CASE-001.json",
    "modernization_readiness_CASE-001.json",
    "modernization_plan_CASE-001.json",
    "capability_registry.json",
    "capability_gap_CASE-003.json",
    "historical_patterns.json",
    "capability_evolution_decision_CASE-001.json",
    "capability_evolution_decision_CASE-003.json",
]


def main() -> int:
    seed_case_001_artifacts()
    repo.record_inventory_shortage_gap()
    seed_capability_evolution_decisions()
    seed_historical_patterns()

    data_dir = ROOT_DIR / "memory" / "data"
    missing = [name for name in EXPECTED_FILES if not (data_dir / name).exists()]
    if missing:
        print(f"ERROR: missing seeded files: {missing}", file=sys.stderr)
        return 1

    print(f"Seeded {len(EXPECTED_FILES)} memory/data files in {data_dir}")
    print(f"All records tagged with source='{DEMO_SOURCE}' (not real production data).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
