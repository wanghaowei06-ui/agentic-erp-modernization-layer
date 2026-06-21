from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="API Validation Gate Support Service", version="0.1.0")


class ValidationRequest(BaseModel):
    simulate_failure: bool = False


class ValidationResponse(BaseModel):
    business_action: str
    data_isolation: str
    rpa_test_case_id: str
    api_test_case_id: str
    same_initial_state: bool
    contract_test: str
    business_rule_test: str
    rpa_api_parity_check: str
    compared_fields: list[str] | None = None
    parity_failure_reason: str | None = None
    trusted_tool_candidate: bool
    requires_registration_approval: bool
    recommended_recovery: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "validation-suite"}


@app.post(
    "/validate/request-purchase-order-approval",
    response_model=ValidationResponse,
    response_model_exclude_none=True,
)
def validate_request_purchase_order_approval(
    payload: ValidationRequest | None = None,
) -> ValidationResponse:
    if payload and payload.simulate_failure:
        return ValidationResponse(
            business_action="request_purchase_order_approval",
            data_isolation="cloned_test_cases",
            rpa_test_case_id="PO-1001-RPA",
            api_test_case_id="PO-1001-API",
            same_initial_state=True,
            contract_test="passed",
            business_rule_test="passed",
            rpa_api_parity_check="failed",
            parity_failure_reason=(
                "Simulated mismatch in audit log creation for demo failure path."
            ),
            trusted_tool_candidate=False,
            requires_registration_approval=False,
            recommended_recovery=(
                "Keep execution mode as RPA, generate fix task, require IT review, "
                "and rerun validation."
            ),
        )

    return ValidationResponse(
        business_action="request_purchase_order_approval",
        data_isolation="cloned_test_cases",
        rpa_test_case_id="PO-1001-RPA",
        api_test_case_id="PO-1001-API",
        same_initial_state=True,
        contract_test="passed",
        business_rule_test="passed",
        rpa_api_parity_check="passed",
        compared_fields=["status", "audit_log_created", "last_action"],
        trusted_tool_candidate=True,
        requires_registration_approval=True,
    )
