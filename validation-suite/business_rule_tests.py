from __future__ import annotations


def validate_budget_exceeded_requires_approval(
    *,
    amount: float,
    budget_limit: float,
    resulting_status: str,
) -> str:
    if amount > budget_limit and resulting_status == "PENDING_MANAGER_APPROVAL":
        return "passed"
    return "failed"
