from __future__ import annotations

REQUIRED_REQUEST_FIELDS = {"approval_reason", "manager_id", "source_case_id"}
REQUIRED_RESPONSE_FIELDS = {
    "po_id",
    "status",
    "audit_log_created",
    "execution_mode",
    "source_case_id",
}


def validate_contract(request_payload: dict, response_payload: dict) -> str:
    if not REQUIRED_REQUEST_FIELDS.issubset(request_payload):
        return "failed"
    if not REQUIRED_RESPONSE_FIELDS.issubset(response_payload):
        return "failed"
    if response_payload.get("execution_mode") != "API":
        return "failed"
    if response_payload.get("audit_log_created") is not True:
        return "failed"
    return "passed"
