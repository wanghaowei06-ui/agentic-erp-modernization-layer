"""Pattern Memory — incremental aggregation per ``process_signature``.

Each time a run is committed, the corresponding pattern file is updated
**incrementally** (not by recomputing across all history). The pattern file
lives at ``memory/patterns/{process_signature}.json``.

``process_signature = business_action + "__" + exception_type + "__" +
route_family + "__" + policy_gate_family + "__" + side_effects_signature``

Seed data from ``seed_historical_memory.py`` (``memory/data/historical_patterns.json``)
is used as the initial values when no pattern file yet exists for a given
signature. After the first real run commit, the dedicated pattern file takes
over and is updated on every subsequent commit.

This module returns a ``before`` / ``after`` snapshot so callers can render
``evolution/pattern_update.json`` for auditability.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from memory import store as _store
from memory.run_memory import (
    SCHEMA_VERSION,
    _read_json,
    _safe_filename,
    _utc_iso,
    _write_json,
    pattern_path,
)


# Maximum number of latest_run_ids to retain in the pattern file.
MAX_LATEST_RUN_IDS = 25

# Default recommendation when a pattern has no clear signal yet.
DEFAULT_RECOMMENDATION = "KEEP_RPA_MODE"


def _load_historical_seed() -> list[dict[str, Any]]:
    """Load the seed historical patterns from ``memory/data`` (legacy file).

    Used only as the initial data source when a real-run pattern file does
    not yet exist for a process_signature.
    """
    # Read DATA_DIR at runtime so monkeypatching in tests is respected.
    legacy_path = _store.DATA_DIR / "historical_patterns.json"
    if not legacy_path.exists():
        return []
    try:
        raw = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    return raw


def _seed_for_signature(
    business_action: str,
    exception_type: str,
) -> dict[str, Any] | None:
    """Return the seed pattern matching the given action/exception, if any."""
    for entry in _load_historical_seed():
        if (
            entry.get("business_action") == business_action
            and entry.get("exception_type") == exception_type
        ):
            # Normalize the seed into the canonical pattern shape.
            return _normalize_seed_to_pattern(entry)
    return None


def _normalize_seed_to_pattern(entry: dict[str, Any]) -> dict[str, Any]:
    """Normalize a legacy seed pattern into the canonical pattern schema."""
    business_action = entry.get("business_action", "")
    exception_type = entry.get("exception_type", "")
    process_signature = entry.get("process_signature") or (
        f"{business_action}__{exception_type}" if business_action and exception_type else ""
    )
    observed_count = int(entry.get("observed_count", 0))
    validation_pass_count = int(entry.get("validation_pass_count", 0))
    manual_handling_count = int(entry.get("manual_handling_count", 0))
    rpa_success_count = int(entry.get("rpa_success_count", 0))
    selector_failure_count = int(entry.get("selector_failure_count", 0))
    validation_fail_count = max(observed_count - validation_pass_count, 0)
    successful_run_count = rpa_success_count + validation_pass_count

    return {
        "process_signature": process_signature,
        "business_action": business_action,
        "exception_type": exception_type,
        "route_family": entry.get("route_family", "legacy_route"),
        "policy_gate_family": entry.get("policy_gate_family", "legacy_policy"),
        "side_effects_family": entry.get("side_effects_family", "legacy_side_effects"),
        "business_remarks_examples": list(entry.get("business_remarks_examples", []) or []),
        "company_context_used_examples": list(entry.get("company_context_used_examples", []) or []),
        "agent_analysis_examples": list(entry.get("agent_analysis_examples", []) or []),
        "observed_count": observed_count,
        "successful_run_count": successful_run_count,
        "manual_handling_count": manual_handling_count,
        "validation_pass_count": validation_pass_count,
        "validation_fail_count": validation_fail_count,
        "selector_failure_count": selector_failure_count,
        "field_stability": float(entry.get("field_stability", 0.0)),
        "side_effect_stability": float(entry.get("side_effect_stability", 0.0)),
        "ui_fragility": float(entry.get("ui_fragility", 0.0)),
        "business_value": float(entry.get("business_value", 0.0)),
        "latest_run_ids": [],
        "current_recommendation": entry.get("recommended_evolution")
        or DEFAULT_RECOMMENDATION,
        "source": "seed_historical_memory",
        "schema_version": SCHEMA_VERSION,
    }


def load_pattern(process_signature: str) -> dict[str, Any]:
    """Load the pattern for ``process_signature``.

    If the dedicated pattern file does not exist, attempt to seed from the
    legacy ``historical_patterns.json``. If no seed matches, return an empty
    pattern shell.
    """
    path = pattern_path(process_signature)
    if path.exists():
        return _read_json(path, {}) or {}
    # Try to seed from legacy historical_patterns.json
    parts = process_signature.split("__")
    if len(parts) >= 2:
        seed = _seed_for_signature(parts[0], parts[1])
        if seed:
            seed["process_signature"] = process_signature
            if len(parts) >= 5:
                seed["route_family"] = parts[2]
                seed["policy_gate_family"] = parts[3]
                seed["side_effects_family"] = parts[4]
            return seed
    return {}


def save_pattern(process_signature: str, pattern: dict[str, Any]) -> Path:
    return _write_json(pattern_path(process_signature), pattern)


def _compute_recommendation(pattern: dict[str, Any]) -> str:
    """Derive the current recommendation from aggregated pattern signals.

    Mirrors the high-level decision tree of the capability-evolution
    evaluator but expressed as a recommendation label only. The authoritative
    decision is still produced by ``evaluate_capability_evolution``.
    """
    observed = int(pattern.get("observed_count", 0))
    business_value = float(pattern.get("business_value", 0.0))
    field_stability = float(pattern.get("field_stability", 0.0))
    validation_pass = int(pattern.get("validation_pass_count", 0))
    validation_fail = int(pattern.get("validation_fail_count", 0))
    selector_failures = int(pattern.get("selector_failure_count", 0))
    validation_pass_rate = (
        validation_pass / observed if observed else 0.0
    )

    if observed >= 5 and business_value >= 0.75 and field_stability >= 0.75 and validation_pass_rate >= 0.8:
        return "API_MODERNIZATION_PROPOSAL"
    if selector_failures >= 3:
        return "XAML_IMPROVEMENT_PROPOSAL"
    if observed >= 3 and validation_pass == 0:
        return "XAML_WORKFLOW_PROPOSAL"
    if field_stability and field_stability < 0.6:
        return "KEEP_RPA_MODE"
    return DEFAULT_RECOMMENDATION


def increment_pattern(
    *,
    business_action: str,
    exception_type: str,
    run_id: str,
    process_signature: str | None = None,
    route_family: str | None = None,
    policy_gate_family: str | None = None,
    side_effects_family: str | None = None,
    business_remarks: str | None = None,
    company_context_used: dict[str, Any] | None = None,
    agent_reasoning_summary: str | None = None,
    result: str | None = None,
    execution_mode: str | None = None,
    field_stability: float | None = None,
    side_effect_stability: float | None = None,
    ui_fragility: float | None = None,
    business_value: float | None = None,
    validation_passed: bool | None = None,
    selector_failure: bool = False,
    manual_handling: bool = False,
) -> dict[str, Any]:
    """Incrementally update the pattern for ``business_action__exception_type``.

    Returns a ``pattern_update`` payload containing ``before`` and ``after``
    snapshots plus the list of fields that changed.

    Increments observed_count by 1 and adjusts the relevant counters based on
    the run outcome. Stability metrics are EMA-smoothed when a new value is
    supplied.
    """
    process_signature = process_signature or f"{business_action}__{exception_type}"
    before = deepcopy(load_pattern(process_signature))

    # If pattern is empty, initialize a fresh shell (possibly seeded next).
    if not before:
        before = _normalize_seed_to_pattern(
            {
                "business_action": business_action,
                "exception_type": exception_type,
                "observed_count": 0,
            }
        )
        # Try to enrich from seed data (preserves historical counts).
        seed = _seed_for_signature(business_action, exception_type)
        if seed:
            before = seed

    after = deepcopy(before)

    # Increment observed count.
    after["observed_count"] = int(before.get("observed_count", 0)) + 1

    # Successful run count.
    success = (
        str(result).upper() == "SUCCESS"
        and str(execution_mode).upper() in {"API", "RPA"}
    )
    if success:
        after["successful_run_count"] = int(before.get("successful_run_count", 0)) + 1

    # Manual handling count.
    if manual_handling or (str(result).upper() in {"MANUAL", "MANUAL_HANDLING"}):
        after["manual_handling_count"] = int(before.get("manual_handling_count", 0)) + 1

    # Validation pass / fail counts.
    if validation_passed is True:
        after["validation_pass_count"] = int(before.get("validation_pass_count", 0)) + 1
    elif validation_passed is False:
        after["validation_fail_count"] = int(before.get("validation_fail_count", 0)) + 1

    # Selector failures.
    if selector_failure:
        after["selector_failure_count"] = int(before.get("selector_failure_count", 0)) + 1

    # EMA-smoothed stability metrics (alpha=0.2 favors history).
    alpha = 0.2
    if field_stability is not None:
        prev = float(before.get("field_stability", 0.0)) or field_stability
        after["field_stability"] = round((1 - alpha) * prev + alpha * float(field_stability), 4)
    if side_effect_stability is not None:
        prev = float(before.get("side_effect_stability", 0.0)) or side_effect_stability
        after["side_effect_stability"] = round(
            (1 - alpha) * prev + alpha * float(side_effect_stability), 4
        )
    if ui_fragility is not None:
        prev = float(before.get("ui_fragility", 0.0)) or ui_fragility
        after["ui_fragility"] = round((1 - alpha) * prev + alpha * float(ui_fragility), 4)
    if business_value is not None:
        prev = float(before.get("business_value", 0.0)) or business_value
        after["business_value"] = round((1 - alpha) * prev + alpha * float(business_value), 4)

    # Latest run ids (bounded queue).
    latest = list(before.get("latest_run_ids", []))
    latest.append(run_id)
    if len(latest) > MAX_LATEST_RUN_IDS:
        latest = latest[-MAX_LATEST_RUN_IDS:]
    after["latest_run_ids"] = latest

    def _append_bounded_list(field: str, value: Any, max_items: int = 5) -> None:
        if value in (None, "", {}, []):
            return
        values = list(after.get(field, []) or [])
        if value not in values:
            values.append(value)
        after[field] = values[-max_items:]

    _append_bounded_list("business_remarks_examples", business_remarks)
    _append_bounded_list("company_context_used_examples", company_context_used)
    _append_bounded_list("agent_analysis_examples", agent_reasoning_summary)

    # Recompute recommendation.
    after["current_recommendation"] = _compute_recommendation(after)

    # Ensure canonical fields are present.
    after["process_signature"] = process_signature
    after["business_action"] = business_action
    after["exception_type"] = exception_type
    after["route_family"] = route_family or after.get("route_family") or "route_unknown"
    after["policy_gate_family"] = (
        policy_gate_family or after.get("policy_gate_family") or "policy_unknown"
    )
    after["side_effects_family"] = (
        side_effects_family or after.get("side_effects_family") or "no_side_effects"
    )
    after["schema_version"] = SCHEMA_VERSION
    after["updated_at"] = _utc_iso()
    after["source"] = "real_run_memory"

    save_pattern(process_signature, after)

    # Compute field-level diff for auditability.
    changed_fields: list[dict[str, Any]] = []
    all_keys = set(before.keys()) | set(after.keys())
    for key in sorted(all_keys):
        if key in {"updated_at", "source"}:
            continue
        old_val = before.get(key)
        new_val = after.get(key)
        if old_val != new_val:
            changed_fields.append(
                {
                    "field": key,
                    "before": old_val,
                    "after": new_val,
                }
            )

    return {
        "process_signature": process_signature,
        "business_action": business_action,
        "exception_type": exception_type,
        "run_id": run_id,
        "before": before,
        "after": after,
        "changed_fields": changed_fields,
        "updated_at": after["updated_at"],
    }


def list_patterns() -> list[dict[str, Any]]:
    """List all persisted pattern files (does not include seed-only entries)."""
    from memory.run_memory import patterns_root

    root = patterns_root()
    if not root.exists():
        return []
    patterns: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        payload = _read_json(path, {})
        if payload:
            patterns.append(payload)
    return patterns


def get_pattern(process_signature: str) -> dict[str, Any] | None:
    """Return the pattern for ``process_signature`` or ``None`` if absent."""
    payload = load_pattern(process_signature)
    return payload or None
