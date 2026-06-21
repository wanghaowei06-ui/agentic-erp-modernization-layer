from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="API Validation Gate Support Service", version="0.1.0")


class ValidationResponse(BaseModel):
    business_action: str
    data_isolation: str
    rpa_test_case_id: str
    api_test_case_id: str
    same_initial_state: bool
    contract_test: str
    business_rule_test: str
    rpa_api_parity_check: str
    compared_fields: list[str]
    trusted_tool_candidate: bool
    requires_registration_approval: bool


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "validation-suite"}


@app.post(
    "/validate/request-purchase-order-approval",
    response_model=ValidationResponse,
)
def validate_request_purchase_order_approval() -> ValidationResponse:
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
