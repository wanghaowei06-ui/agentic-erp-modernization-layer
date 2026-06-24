from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Literal, TypedDict

from fastapi import FastAPI
from pydantic import BaseModel, Field, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas import TriageRequest as StableTriageRequest
from schemas import TriageResponse as StableTriageResponse
from shared.automation_memory.repository import record_agent_decision
from triage import classify_exception

app = FastAPI(title="ERP Exception Triage Support Service", version="0.2.0")
logger = logging.getLogger("reasoning-agent.llm")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    logger.addHandler(handler)

memory_logger = logging.getLogger("reasoning-agent.memory")
memory_logger.setLevel(logging.INFO)
if not memory_logger.handlers:
    memory_handler = logging.StreamHandler()
    memory_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    memory_logger.addHandler(memory_handler)

try:
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - package availability depends on runtime env
    END = START = StateGraph = None
    LANGGRAPH_AVAILABLE = False


SIDE_EFFECTS_SIGNATURE = [
    "PO_STATUS_UPDATED",
    "APPROVAL_TASK_CREATED",
    "AUDIT_LOG_CREATED",
    "MANAGER_NOTIFICATION_QUEUED",
    "BUDGET_REVIEW_FLAGGED",
]

CANONICAL_TRIAGE_STAGES = {
    "budget_exceeded": "WAITING_FOR_HUMAN_APPROVAL",
    "vendor_info_missing": "WAITING_VENDOR_INFO",
    "inventory_shortage": "WAITING_INVENTORY_REVIEW",
    "unknown_exception": "WAITING_MANUAL_INVESTIGATION",
}

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


def load_dotenv_file() -> None:
    if os.getenv("SKIP_DOTENV_LOAD") == "1":
        return
    for path in [
        Path(__file__).resolve().parents[2] / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_dotenv_file()


class AgentMetadata(BaseModel):
    agent_runtime: str = "langgraph"
    reasoning_mode: str = "llm_backed"
    llm_enabled: bool
    model: str | None = None
    schema_validated: bool = False
    guardrails_applied: bool = False
    decision_status: str = "DECISION_READY"
    llm_call_mode: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_request_id: str | None = None
    llm_latency_ms: int | None = None
    llm_invocation_verified: bool = False


class TriageRequest(StableTriageRequest):
    pass


class LegacyTriageRequest(BaseModel):
    case_id: str
    po_id: str
    amount: float
    budget_limit: float
    vendor_id: str | None = None
    vendor_info_complete: bool
    inventory_available: bool
    erp_status: str
    raw_exception_text: str


class TriageResponse(StableTriageResponse):
    pass


class LegacyTriageResponse(BaseModel):
    case_id: str
    po_id: str
    detected_exception_type: str
    risk_level: str
    confidence: float = Field(ge=0, le=1)
    recommended_path: str
    requires_human_approval: bool
    next_action: str = "manual_investigation"
    next_stage: str
    reasoning_summary: str
    evidence: list[dict[str, Any]]
    fallback: str = "manual_investigation"
    agent_runtime: str = "langgraph"
    reasoning_mode: str = "llm_backed"
    llm_enabled: bool = True
    model: str | None = None
    schema_validated: bool = True
    guardrails_applied: bool = True
    decision_status: str = "DECISION_READY"
    blocking_reasons: list[str] = []
    llm_call_mode: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_request_id: str | None = None
    llm_latency_ms: int | None = None
    llm_invocation_verified: bool = False


class ModernizationReadinessRequest(BaseModel):
    case_id: str
    po_id: str
    business_action: str
    detected_exception_type: str
    frequency_30d: int
    rpa_writeback_status: str
    validation_status: str
    human_approval: str
    side_effects_observed: bool = True
    rpa_api_parity_required: bool = True
    rpa_api_parity_check: str = "passed"


class ModernizationReadinessResponse(BaseModel):
    case_id: str
    business_action: str
    modernization_candidate: bool
    readiness_score: float
    frequency_score: float
    risk_score: float
    recommended_api_tool_name: str
    recommended_next_stage: str
    reasoning_summary: str
    blocking_reasons: list[str]
    side_effects_observed: bool
    rpa_api_parity_required: bool
    agent_runtime: str = "langgraph"
    reasoning_mode: str = "llm_backed"
    llm_enabled: bool = True
    model: str | None = None
    schema_validated: bool = True
    guardrails_applied: bool = True
    decision_status: str = "DECISION_READY"
    llm_call_mode: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_request_id: str | None = None
    llm_latency_ms: int | None = None
    llm_invocation_verified: bool = False


class ModernizationPlanRequest(BaseModel):
    case_id: str
    business_action: str
    modernization_candidate: bool = True
    recommended_api_tool_name: str | None = None
    readiness_score: float | None = None


class ModernizationPlanResponse(BaseModel):
    plan_id: str
    case_id: str
    target_tool_name: str
    target_service: str
    proposed_endpoint: str
    source_rpa_trace: str
    contract_requirements: list[str]
    tests_required: list[str]
    side_effects_signature: list[str]
    rpa_api_parity_required: bool
    risk_level: str
    requires_engineer_approval: bool
    recommended_next_stage: str
    agent_runtime: str = "langgraph"
    reasoning_mode: str = "llm_backed"
    llm_enabled: bool = True
    model: str | None = None
    schema_validated: bool = True
    guardrails_applied: bool = True
    decision_status: str = "DECISION_READY"
    blocking_reasons: list[str] = []
    llm_call_mode: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_request_id: str | None = None
    llm_latency_ms: int | None = None
    llm_invocation_verified: bool = False


class TriageDecision(BaseModel):
    detected_exception_type: Literal[
        "budget_exceeded",
        "vendor_info_missing",
        "inventory_shortage",
        "unknown_exception",
    ]
    risk_level: Literal["low", "medium", "high", "unknown"]
    requires_human_approval: bool
    next_stage: str
    reasoning_summary: str
    confidence: float = Field(ge=0, le=1)


class ReadinessDecision(BaseModel):
    modernization_candidate: bool
    readiness_score: float = Field(ge=0, le=1)
    recommended_api_tool_name: str
    recommended_next_stage: str
    reasoning_summary: str
    blocking_reasons: list[str] = []


class PlanDecision(BaseModel):
    plan_id: str
    target_tool_name: str
    target_service: str
    proposed_endpoint: str
    source_rpa_trace: str
    contract_requirements: list[str]
    tests_required: list[str]
    risk_level: Literal["low", "medium", "high"]
    requires_engineer_approval: bool
    recommended_next_stage: str


class LlmConfig(BaseModel):
    provider: str
    api_key: str | None
    model: str
    base_url: str
    timeout_seconds: float
    max_retries: int
    demo_mode: str | None


class LlmCallEvidence(BaseModel):
    llm_call_mode: Literal["real", "mock"]
    llm_provider: str
    llm_model: str
    llm_request_id: str
    llm_latency_ms: int
    llm_invocation_verified: bool


class StructuredLlmResult(BaseModel):
    value: Any
    evidence: LlmCallEvidence


class AgentState(TypedDict, total=False):
    payload: Any
    prompt: str
    raw_output: str
    validated_output: Any
    response: Any
    errors: list[str]
    llm_evidence: LlmCallEvidence


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "reasoning-agent"}


@app.get("/llm/status")
def llm_status() -> dict[str, object]:
    config = load_llm_config()
    return {
        "provider": config.provider,
        "model": config.model,
        "base_url_configured": bool(config.base_url),
        "api_key_configured": bool(config.api_key),
        "demo_mode": config.demo_mode,
        "real_llm_enabled": bool(config.api_key) and config.demo_mode != "mock_success",
    }


def load_llm_config() -> LlmConfig:
    return LlmConfig(
        provider=os.getenv("LLM_PROVIDER", "deepseek"),
        api_key=os.getenv("LLM_API_KEY"),
        model=os.getenv("LLM_MODEL", DEFAULT_MODEL),
        base_url=os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "15")),
        max_retries=max(int(os.getenv("LLM_MAX_RETRIES", "2")), 1),
        demo_mode=os.getenv("LLM_DEMO_MODE"),
    )


def metadata(
    *,
    config: LlmConfig,
    llm_enabled: bool,
    schema_validated: bool,
    guardrails_applied: bool,
    decision_status: str,
    evidence: LlmCallEvidence | None = None,
    invocation_verified: bool | None = None,
) -> dict[str, Any]:
    return AgentMetadata(
        llm_enabled=llm_enabled,
        model=config.model if llm_enabled else None,
        schema_validated=schema_validated,
        guardrails_applied=guardrails_applied,
        decision_status=decision_status,
        llm_call_mode=evidence.llm_call_mode if evidence else None,
        llm_provider=evidence.llm_provider if evidence else config.provider,
        llm_model=evidence.llm_model if evidence else (config.model if llm_enabled else None),
        llm_request_id=evidence.llm_request_id if evidence else None,
        llm_latency_ms=evidence.llm_latency_ms if evidence else None,
        llm_invocation_verified=(
            invocation_verified
            if invocation_verified is not None
            else bool(evidence and evidence.llm_invocation_verified)
        ),
    ).model_dump()


def model_unavailable_metadata(config: LlmConfig) -> dict[str, Any]:
    return metadata(
        config=config,
        llm_enabled=False,
        schema_validated=False,
        guardrails_applied=True,
        decision_status="MODEL_UNAVAILABLE",
    )


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM output must be a JSON object")
    return parsed


def llm_available(config: LlmConfig) -> bool:
    return config.demo_mode == "mock_success" or bool(config.api_key)


def call_llm_json(
    prompt: str,
    config: LlmConfig,
    agent_name: str,
    request_id: str,
) -> tuple[str, LlmCallEvidence]:
    start = time.perf_counter()
    if config.demo_mode == "mock_success":
        logger.info(
            "[LLM] provider=%s model=%s mode=mock endpoint=/%s request_id=%s",
            config.provider,
            config.model,
            agent_name,
            request_id,
        )
        raw = json.dumps(mock_llm_success(prompt, agent_name))
        latency_ms = int((time.perf_counter() - start) * 1000)
        return raw, LlmCallEvidence(
            llm_call_mode="mock",
            llm_provider=config.provider,
            llm_model=config.model,
            llm_request_id=request_id,
            llm_latency_ms=latency_ms,
            llm_invocation_verified=True,
        )
    if not config.api_key:
        raise RuntimeError("LLM_API_KEY is not configured")

    endpoint = f"{config.base_url}/chat/completions"
    logger.info(
        "[LLM] provider=%s model=%s mode=real endpoint=/%s request_id=%s",
        config.provider,
        config.model,
        agent_name,
        request_id,
    )
    logger.info("[LLM] real API call started request_id=%s", request_id)
    body = {
        "model": config.model,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a UiPath-governed ERP modernization support agent. "
                    "Return only valid JSON matching the requested schema. "
                    "Do not perform orchestration; UiPath owns case orchestration."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        status = response.status
        payload = json.loads(response.read().decode("utf-8"))
    latency_ms = int((time.perf_counter() - start) * 1000)
    logger.info(
        "[LLM] real API call completed status=%s latency_ms=%s request_id=%s",
        status,
        latency_ms,
        request_id,
    )
    return payload["choices"][0]["message"]["content"], LlmCallEvidence(
        llm_call_mode="real",
        llm_provider=config.provider,
        llm_model=config.model,
        llm_request_id=request_id,
        llm_latency_ms=latency_ms,
        llm_invocation_verified=True,
    )


def call_llm_with_retries(prompt: str, config: LlmConfig, agent_name: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(config.max_retries):
        try:
            raw_output, _ = call_llm_json(
                prompt, config, agent_name, f"{agent_name}-{uuid.uuid4().hex[:12]}"
            )
            return extract_json_object(raw_output)
        except (
            KeyError,
            ValueError,
            json.JSONDecodeError,
            RuntimeError,
            urllib.error.URLError,
            TimeoutError,
        ) as exc:
            last_error = exc
            if attempt + 1 < config.max_retries:
                time.sleep(0.1)
    raise RuntimeError(f"LLM output unavailable or invalid: {last_error}")


def call_llm_structured(
    prompt: str,
    config: LlmConfig,
    agent_name: str,
    schema_model: type[BaseModel],
) -> StructuredLlmResult:
    last_error: Exception | None = None
    correction_prompt = prompt
    request_id = f"{agent_name}-{uuid.uuid4().hex[:12]}"
    for attempt in range(config.max_retries):
        try:
            llm_result = call_llm_json(
                correction_prompt, config, agent_name, request_id
            )
            if isinstance(llm_result, tuple):
                raw_output, evidence = llm_result
            else:
                raw_output = llm_result
                evidence = LlmCallEvidence(
                    llm_call_mode=(
                        "mock" if config.demo_mode == "mock_success" else "real"
                    ),
                    llm_provider=config.provider,
                    llm_model=config.model,
                    llm_request_id=request_id,
                    llm_latency_ms=0,
                    llm_invocation_verified=True,
                )
            parsed = extract_json_object(raw_output)
            if schema_model is PlanDecision and isinstance(parsed.get("risk_level"), str):
                parsed["risk_level"] = parsed["risk_level"].lower()
            value = schema_model.model_validate(parsed)
            logger.info("[LLM] response schema validation passed request_id=%s", request_id)
            return StructuredLlmResult(value=value, evidence=evidence)
        except (
            ValidationError,
            ValueError,
            json.JSONDecodeError,
            RuntimeError,
            urllib.error.URLError,
            TimeoutError,
        ) as exc:
            last_error = exc
            if attempt + 1 < config.max_retries:
                correction_prompt = (
                    prompt
                    + "\n\nPrevious model output failed validation. "
                    + f"Validation error: {exc}. "
                    + "Retry with only a valid JSON object. Use exact enum values. "
                    + "Numeric fields such as confidence and readiness_score must be numbers, not labels."
                )
                time.sleep(0.1)
    raise RuntimeError(f"LLM schema validation failed after retry: {last_error}")


def mock_llm_success(prompt: str, agent_name: str) -> dict[str, Any]:
    if agent_name == "triage":
        context = json.loads(prompt.split("CASE_DATA_JSON:", 1)[1].strip())
        vendor_missing = (
            not context["vendor_info_complete"]
            or context.get("vendor_id") is None
            or str(context.get("vendor_id")).strip() == ""
        )
        if context["amount"] > context["budget_limit"]:
            return {
                "detected_exception_type": "budget_exceeded",
                "risk_level": "high",
                "requires_human_approval": True,
                "next_stage": "WAITING_FOR_HUMAN_APPROVAL",
                "reasoning_summary": (
                    "The amount is above the budget limit, so manager approval is required."
                ),
                "confidence": 0.94,
            }
        if vendor_missing:
            return {
                "detected_exception_type": "vendor_info_missing",
                "risk_level": "medium",
                "requires_human_approval": False,
                "next_stage": "WAITING_VENDOR_INFO",
                "reasoning_summary": "Vendor fields are incomplete, so vendor information is required.",
                "confidence": 0.91,
            }
        if not context["inventory_available"]:
            return {
                "detected_exception_type": "inventory_shortage",
                "risk_level": "medium",
                "requires_human_approval": True,
                "next_stage": "WAITING_INVENTORY_REVIEW",
                "reasoning_summary": "Inventory is unavailable, so inventory review is required.",
                "confidence": 0.88,
            }
        return {
            "detected_exception_type": "unknown_exception",
            "risk_level": "unknown",
            "requires_human_approval": True,
            "next_stage": "WAITING_MANUAL_INVESTIGATION",
            "reasoning_summary": "No supported exception type is clear from the fields.",
            "confidence": 0.5,
        }
    if agent_name == "readiness":
        return {
            "modernization_candidate": True,
            "readiness_score": 0.91,
            "recommended_api_tool_name": "request_purchase_order_approval",
            "recommended_next_stage": "CREATE_MODERNIZATION_PLAN",
            "reasoning_summary": (
                "The action is frequent, deterministic, human-approved, validation-passed, "
                "and eligible only after side effects parity passes."
            ),
            "blocking_reasons": [],
        }
    if agent_name == "plan":
        return {
            "plan_id": "MOD-PLAN-001",
            "target_tool_name": "request_purchase_order_approval",
            "target_service": "generated-api-facade",
            "proposed_endpoint": "POST /api/purchase-orders/{po_id}/approval-request",
            "source_rpa_trace": (
                "UiPath clicked Request Approval in Mock Legacy ERP and observed "
                "PENDING_MANAGER_APPROVAL plus the standard side effects signature."
            ),
            "contract_requirements": [
                "must return po_id",
                "must return status=PENDING_MANAGER_APPROVAL",
                "must return audit_log_created=true",
                "must return execution_mode=API",
                "must preserve source_case_id",
                "must return the complete side_effects signature",
            ],
            "tests_required": [
                "contract_test",
                "business_rule_test",
                "rpa_api_parity_check",
            ],
            "risk_level": "medium",
            "requires_engineer_approval": True,
            "recommended_next_stage": "AUTOMATION_OWNER_PLAN_REVIEW",
        }
    raise ValueError(f"Unknown agent name {agent_name}")


def recommended_path_for(exception_type: str) -> str:
    return {
        "budget_exceeded": "manager_approval_required",
        "vendor_info_missing": "vendor_information_request",
        "inventory_shortage": "inventory_review_required",
        "unknown_exception": "manual_investigation",
    }.get(exception_type, "manual_investigation")


def triage_blocking_reasons(payload: TriageRequest, decision: TriageDecision) -> list[str]:
    reasons: list[str] = []
    vendor_missing = (
        not payload.vendor_info_complete
        or payload.vendor_id is None
        or payload.vendor_id.strip() == ""
    )
    if payload.amount > payload.budget_limit and decision.detected_exception_type != "budget_exceeded":
        reasons.append("amount exceeds budget_limit but LLM did not classify budget_exceeded")
    if vendor_missing and payload.amount <= payload.budget_limit and decision.detected_exception_type != "vendor_info_missing":
        reasons.append("vendor information is missing but LLM did not classify vendor_info_missing")
    if (
        not payload.inventory_available
        and payload.amount <= payload.budget_limit
        and not vendor_missing
        and decision.detected_exception_type != "inventory_shortage"
    ):
        reasons.append("inventory is unavailable but LLM did not classify inventory_shortage")
    if decision.confidence < 0.5:
        reasons.append("LLM confidence is below the minimum decision threshold")
    expected_stage = CANONICAL_TRIAGE_STAGES[decision.detected_exception_type]
    if decision.next_stage != expected_stage:
        reasons.append(
            f"next_stage must be {expected_stage} for {decision.detected_exception_type}"
        )
    return reasons


def readiness_blocking_reasons(payload: ModernizationReadinessRequest) -> list[str]:
    reasons: list[str] = []
    if payload.frequency_30d < 10:
        reasons.append("frequency_30d below modernization threshold")
    if payload.validation_status != "VALIDATION_PASSED":
        reasons.append("validation_status is not VALIDATION_PASSED")
    if payload.human_approval != "APPROVED":
        reasons.append("human_approval is not APPROVED")
    if payload.detected_exception_type != "budget_exceeded":
        reasons.append("exception type is not budget_exceeded")
    if payload.rpa_writeback_status != "PENDING_MANAGER_APPROVAL":
        reasons.append("RPA write-back did not reach PENDING_MANAGER_APPROVAL")
    if payload.side_effects_observed is not True:
        reasons.append("side effects were not observed on the RPA path")
    if payload.rpa_api_parity_required is not True:
        reasons.append("RPA/API side effects parity was not required")
    if payload.rpa_api_parity_check != "passed":
        reasons.append("RPA/API parity check did not pass")
    return reasons


def build_triage_prompt(payload: TriageRequest) -> str:
    return (
        "Classify the ERP exception. Return JSON with detected_exception_type, "
        "risk_level, requires_human_approval, next_stage, reasoning_summary, confidence. "
        "confidence must be a number between 0.0 and 1.0, not a word. "
        "Allowed detected_exception_type values: budget_exceeded, vendor_info_missing, "
        "inventory_shortage, unknown_exception. Use these exact next_stage mappings: "
        "budget_exceeded=WAITING_FOR_HUMAN_APPROVAL, "
        "vendor_info_missing=WAITING_VENDOR_INFO, "
        "inventory_shortage=WAITING_INVENTORY_REVIEW, "
        "unknown_exception=WAITING_MANUAL_INVESTIGATION. CASE_DATA_JSON:"
        + payload.model_dump_json()
    )


def build_readiness_prompt(payload: ModernizationReadinessRequest) -> str:
    return (
        "Evaluate whether this UiPath-observed business action is ready to become an "
        "approved API tool. Return JSON with modernization_candidate, readiness_score, "
        "recommended_api_tool_name, recommended_next_stage, reasoning_summary, "
        "blocking_reasons. API modernization is allowed only after side effects parity "
        "passes. If modernization_candidate is true, use recommended_api_tool_name="
        f"{payload.business_action} and recommended_next_stage=CREATE_MODERNIZATION_PLAN. "
        "CASE_DATA_JSON:"
        + payload.model_dump_json()
    )


def build_plan_prompt(payload: ModernizationPlanRequest) -> str:
    return (
        "Generate a modernization plan for a UiPath-governed API candidate. Return JSON "
        "with plan_id, target_tool_name, target_service, proposed_endpoint, "
        "source_rpa_trace, contract_requirements, tests_required, risk_level, "
        "requires_engineer_approval, recommended_next_stage. The plan must require "
        "RPA/API side effects parity. Use exact values: plan_id=MOD-PLAN-001, "
        f"target_tool_name={payload.recommended_api_tool_name or payload.business_action}, "
        "target_service=generated-api-facade, "
        "proposed_endpoint=POST /api/purchase-orders/{po_id}/approval-request, "
        "risk_level=medium, recommended_next_stage=AUTOMATION_OWNER_PLAN_REVIEW. "
        "CASE_DATA_JSON:"
        + payload.model_dump_json()
    )


def triage_fail_closed(
    payload: TriageRequest,
    config: LlmConfig,
    decision_status: str,
    blocking_reasons: list[str] | None = None,
    evidence: LlmCallEvidence | None = None,
) -> TriageResponse:
    return TriageResponse(
        case_id=payload.case_id,
        po_id=payload.po_id,
        detected_exception_type="unknown_exception",
        risk_level="unknown",
        confidence=0.0,
        recommended_path="manual_investigation",
        next_action="manual_investigation",
        requires_human_approval=True,
        next_stage="WAITING_MANUAL_INVESTIGATION",
        reasoning_summary=(
            "LLM-backed triage could not produce a validated decision. "
            "UiPath should route this case to manual review and must not continue API mode."
        ),
        evidence=evidence_for(payload),
        blocking_reasons=blocking_reasons or [],
        **metadata(
            config=config,
            llm_enabled=bool(config.api_key or config.demo_mode == "mock_success"),
            schema_validated=False,
            guardrails_applied=True,
            decision_status=decision_status,
            evidence=evidence,
            invocation_verified=False,
        ),
    )


def readiness_fail_closed(
    payload: ModernizationReadinessRequest,
    config: LlmConfig,
    decision_status: str,
    blocking_reasons: list[str] | None = None,
    evidence: LlmCallEvidence | None = None,
) -> ModernizationReadinessResponse:
    return ModernizationReadinessResponse(
        case_id=payload.case_id,
        business_action=payload.business_action,
        modernization_candidate=False,
        readiness_score=0.0,
        frequency_score=round(min(payload.frequency_30d / 48, 1.0), 2),
        risk_score=1.0,
        recommended_api_tool_name=payload.business_action,
        recommended_next_stage="WAITING_MANUAL_REVIEW",
        reasoning_summary=(
            "LLM-backed modernization readiness could not produce a validated approval. "
            "UiPath should keep execution in RPA mode and route to manual review."
        ),
        blocking_reasons=blocking_reasons or [],
        side_effects_observed=payload.side_effects_observed,
        rpa_api_parity_required=payload.rpa_api_parity_required,
        **metadata(
            config=config,
            llm_enabled=bool(config.api_key or config.demo_mode == "mock_success"),
            schema_validated=False,
            guardrails_applied=True,
            decision_status=decision_status,
            evidence=evidence,
            invocation_verified=False,
        ),
    )


def plan_fail_closed(
    payload: ModernizationPlanRequest,
    config: LlmConfig,
    decision_status: str,
    blocking_reasons: list[str] | None = None,
    evidence: LlmCallEvidence | None = None,
) -> ModernizationPlanResponse:
    return ModernizationPlanResponse(
        plan_id="PLAN_GENERATION_FAILED",
        case_id=payload.case_id,
        target_tool_name=payload.recommended_api_tool_name or payload.business_action,
        target_service="generated-api-facade",
        proposed_endpoint="",
        source_rpa_trace="",
        contract_requirements=[],
        tests_required=[],
        side_effects_signature=SIDE_EFFECTS_SIGNATURE,
        rpa_api_parity_required=True,
        risk_level="high",
        requires_engineer_approval=True,
        recommended_next_stage="WAITING_MANUAL_REVIEW",
        blocking_reasons=blocking_reasons or [],
        **metadata(
            config=config,
            llm_enabled=bool(config.api_key or config.demo_mode == "mock_success"),
            schema_validated=False,
            guardrails_applied=True,
            decision_status=decision_status,
            evidence=evidence,
            invocation_verified=False,
        ),
    )


def evidence_for(payload: TriageRequest) -> list[dict[str, Any]]:
    return [
        {"field": "amount", "value": payload.amount, "source": "legacy_erp_screen"},
        {
            "field": "budget_limit",
            "value": payload.budget_limit,
            "source": "legacy_erp_screen",
        },
        {
            "field": "vendor_id",
            "value": payload.vendor_id,
            "source": "legacy_erp_screen",
        },
        {
            "field": "vendor_info_complete",
            "value": payload.vendor_info_complete,
            "source": "legacy_erp_screen",
        },
        {
            "field": "inventory_available",
            "value": payload.inventory_available,
            "source": "legacy_erp_screen",
        },
        {
            "field": "raw_exception_text",
            "value": payload.raw_exception_text,
            "source": "legacy_erp_screen",
        },
    ]


def run_triage_agent(payload: TriageRequest) -> TriageResponse:
    config = load_llm_config()
    if not llm_available(config):
        return triage_fail_closed(payload, config, "MODEL_UNAVAILABLE")

    def build_prompt(state: AgentState) -> AgentState:
        return {"prompt": build_triage_prompt(state["payload"])}

    def llm_triage(state: AgentState) -> AgentState:
        try:
            result = call_llm_structured(
                state["prompt"], config, "triage", TriageDecision
            )
            decision = result.value
            return {
                "raw_output": decision.model_dump_json(),
                "validated_output": decision,
                "llm_evidence": result.evidence,
            }
        except RuntimeError as exc:
            return {"errors": [str(exc)]}

    def validate_schema(state: AgentState) -> AgentState:
        if state.get("errors"):
            return state
        if state.get("validated_output"):
            return state
        try:
            decision = TriageDecision.model_validate_json(state["raw_output"])
            return {"validated_output": decision}
        except ValidationError as exc:
            return {"errors": [f"triage schema validation failed: {exc}"]}

    def apply_guardrails(state: AgentState) -> AgentState:
        if state.get("errors"):
            return {"response": triage_fail_closed(payload, config, "INVALID_LLM_OUTPUT", state["errors"])}
        decision: TriageDecision = state["validated_output"]
        reasons = triage_blocking_reasons(payload, decision)
        if reasons:
            return {
                "response": triage_fail_closed(
                    payload,
                    config,
                    "GUARDRAIL_BLOCKED",
                    reasons,
                    state.get("llm_evidence"),
                )
            }
        logger.info("[LLM] guardrails applied request_id=%s", state["llm_evidence"].llm_request_id)
        return {
            "response": TriageResponse(
                case_id=payload.case_id,
                po_id=payload.po_id,
                detected_exception_type=decision.detected_exception_type,
                risk_level=decision.risk_level,
                confidence=decision.confidence,
                recommended_path=recommended_path_for(decision.detected_exception_type),
                next_action={
                    "budget_exceeded": "request_manager_approval",
                    "vendor_info_missing": "request_vendor_information",
                    "inventory_shortage": "create_capability_gap_proposal",
                    "unknown_exception": "manual_investigation",
                }.get(decision.detected_exception_type, "manual_investigation"),
                requires_human_approval=decision.requires_human_approval,
                next_stage=decision.next_stage,
                reasoning_summary=decision.reasoning_summary,
                evidence=evidence_for(payload),
                **metadata(
                    config=config,
                    llm_enabled=True,
                    schema_validated=True,
                    guardrails_applied=True,
                    decision_status="DECISION_READY",
                    evidence=state["llm_evidence"],
                    invocation_verified=True,
                ),
            )
        }

    return invoke_graph(
        AgentState,
        [
            ("build_prompt", build_prompt),
            ("llm_triage", llm_triage),
            ("validate_schema", validate_schema),
            ("apply_guardrails", apply_guardrails),
        ],
        {"payload": payload},
    )["response"]


def run_readiness_agent(payload: ModernizationReadinessRequest) -> ModernizationReadinessResponse:
    config = load_llm_config()
    if not llm_available(config):
        return readiness_fail_closed(payload, config, "MODEL_UNAVAILABLE")

    def collect_evidence(state: AgentState) -> AgentState:
        return {"prompt": build_readiness_prompt(state["payload"])}

    def llm_readiness(state: AgentState) -> AgentState:
        try:
            result = call_llm_structured(
                state["prompt"], config, "readiness", ReadinessDecision
            )
            decision = result.value
            return {
                "raw_output": decision.model_dump_json(),
                "validated_output": decision,
                "llm_evidence": result.evidence,
            }
        except RuntimeError as exc:
            return {"errors": [str(exc)]}

    def validate_schema(state: AgentState) -> AgentState:
        if state.get("errors"):
            return state
        if state.get("validated_output"):
            return state
        try:
            decision = ReadinessDecision.model_validate_json(state["raw_output"])
            return {"validated_output": decision}
        except ValidationError as exc:
            return {"errors": [f"readiness schema validation failed: {exc}"]}

    def apply_guardrails(state: AgentState) -> AgentState:
        if state.get("errors"):
            return {
                "response": readiness_fail_closed(
                    payload, config, "INVALID_LLM_OUTPUT", state["errors"]
                )
            }
        decision: ReadinessDecision = state["validated_output"]
        guardrail_reasons = readiness_blocking_reasons(payload)
        blocking_reasons = list(decision.blocking_reasons) + guardrail_reasons
        allowed = decision.modernization_candidate and not guardrail_reasons
        frequency_score = round(min(payload.frequency_30d / 48, 1.0), 2)
        if not allowed:
            blocking_reasons = blocking_reasons or [
                "LLM did not recommend this action as a modernization candidate"
            ]
        logger.info("[LLM] guardrails applied request_id=%s", state["llm_evidence"].llm_request_id)
        return {
            "response": ModernizationReadinessResponse(
                case_id=payload.case_id,
                business_action=payload.business_action,
                modernization_candidate=allowed,
                readiness_score=round(decision.readiness_score if allowed else min(decision.readiness_score, 0.59), 2),
                frequency_score=frequency_score,
                risk_score=0.22 if payload.detected_exception_type == "budget_exceeded" else 0.48,
                recommended_api_tool_name=payload.business_action,
                recommended_next_stage=(
                    "CREATE_MODERNIZATION_PLAN" if allowed else "KEEP_RPA_AND_REVIEW"
                ),
                reasoning_summary=(
                    decision.reasoning_summary
                    if allowed
                    else "LLM recommendation was blocked by deterministic guardrails. UiPath should keep execution in RPA mode and review."
                ),
                blocking_reasons=blocking_reasons,
                side_effects_observed=payload.side_effects_observed,
                rpa_api_parity_required=payload.rpa_api_parity_required,
                **metadata(
                    config=config,
                    llm_enabled=True,
                    schema_validated=True,
                    guardrails_applied=True,
                    decision_status="DECISION_READY" if allowed else "GUARDRAIL_BLOCKED",
                    evidence=state["llm_evidence"],
                    invocation_verified=allowed,
                ),
            )
        }

    return invoke_graph(
        AgentState,
        [
            ("collect_evidence", collect_evidence),
            ("llm_readiness", llm_readiness),
            ("validate_schema", validate_schema),
            ("apply_guardrails", apply_guardrails),
        ],
        {"payload": payload},
    )["response"]


def run_plan_agent(payload: ModernizationPlanRequest) -> ModernizationPlanResponse:
    config = load_llm_config()
    if not llm_available(config):
        return plan_fail_closed(payload, config, "MODEL_UNAVAILABLE")

    def build_plan_context(state: AgentState) -> AgentState:
        return {"prompt": build_plan_prompt(state["payload"])}

    def llm_generate_plan(state: AgentState) -> AgentState:
        try:
            result = call_llm_structured(state["prompt"], config, "plan", PlanDecision)
            plan = result.value
            return {
                "raw_output": plan.model_dump_json(),
                "validated_output": plan,
                "llm_evidence": result.evidence,
            }
        except RuntimeError as exc:
            return {"errors": [str(exc)]}

    def validate_plan_schema(state: AgentState) -> AgentState:
        if state.get("errors"):
            return {
                "response": plan_fail_closed(
                    payload, config, "PLAN_GENERATION_FAILED", state["errors"]
                )
            }
        try:
            plan = state.get("validated_output") or PlanDecision.model_validate_json(
                state["raw_output"]
            )
        except ValidationError as exc:
            return {
                "response": plan_fail_closed(
                    payload,
                    config,
                    "PLAN_GENERATION_FAILED",
                    [f"plan schema validation failed: {exc}"],
                )
            }
        logger.info("[LLM] guardrails applied request_id=%s", state["llm_evidence"].llm_request_id)
        return {
            "response": ModernizationPlanResponse(
                plan_id="MOD-PLAN-001",
                case_id=payload.case_id,
                target_tool_name=payload.recommended_api_tool_name or payload.business_action,
                target_service="generated-api-facade",
                proposed_endpoint="POST /api/purchase-orders/{po_id}/approval-request",
                source_rpa_trace=plan.source_rpa_trace,
                contract_requirements=plan.contract_requirements,
                tests_required=plan.tests_required,
                side_effects_signature=SIDE_EFFECTS_SIGNATURE,
                rpa_api_parity_required=True,
                risk_level="medium",
                requires_engineer_approval=plan.requires_engineer_approval,
                recommended_next_stage="AUTOMATION_OWNER_PLAN_REVIEW",
                **metadata(
                    config=config,
                    llm_enabled=True,
                    schema_validated=True,
                    guardrails_applied=True,
                    decision_status="DECISION_READY",
                    evidence=state["llm_evidence"],
                    invocation_verified=True,
                ),
            )
        }

    return invoke_graph(
        AgentState,
        [
            ("build_plan_context", build_plan_context),
            ("llm_generate_plan", llm_generate_plan),
            ("validate_plan_schema", validate_plan_schema),
        ],
        {"payload": payload},
    )["response"]


def invoke_graph(
    state_type: type[AgentState],
    nodes: list[tuple[str, Any]],
    initial_state: AgentState,
) -> AgentState:
    if not LANGGRAPH_AVAILABLE:
        state = initial_state
        for _, node in nodes:
            state = {**state, **node(state)}
        return state
    graph = StateGraph(state_type)
    previous = START
    for name, node in nodes:
        graph.add_node(name, node)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile().invoke(initial_state)


def triage_case_id(payload: TriageRequest, response: TriageResponse) -> str:
    if response.case_id:
        return response.case_id
    if payload.po_id:
        return f"CASE-{payload.po_id}"
    return "CASE-UNKNOWN"


def triage_input_summary(payload: TriageRequest) -> dict[str, Any]:
    return {
        "po_id": payload.po_id,
        "amount": payload.amount,
        "budget_limit": payload.budget_limit,
        "vendor_id": payload.vendor_id,
        "available_inventory": payload.inventory_available,
    }


def triage_memory_payload(
    payload: TriageRequest,
    response: TriageResponse,
) -> dict[str, Any]:
    response_dict = response.model_dump()
    return {
        "input_summary": triage_input_summary(payload),
        "decision_output": response_dict,
        "po_id": response.po_id,
        "detected_exception_type": response.detected_exception_type,
        "risk_level": response.risk_level,
        "confidence": response.confidence,
        "decision_source": response.decision_source,
        "business_action": response.business_action,
        "required_approval_type": response.required_approval_type,
        "recommended_next_stage": response.recommended_next_stage,
        "capability_lookup_required": response.capability_lookup_required,
        "guardrail_status": response.guardrail_status,
        "evidence": [item.model_dump() for item in response.evidence],
        "fallback": response.fallback,
    }


def record_triage_memory(
    payload: TriageRequest,
    response: TriageResponse,
) -> TriageResponse:
    try:
        event = record_agent_decision(
            triage_case_id(payload, response),
            triage_memory_payload(payload, response),
            source_service="reasoning-agent",
            correlation_id=response.correlation_id,
        )
    except Exception as exc:  # pragma: no cover - exercised by failure test
        memory_logger.warning("Automation Memory triage write failed: %s", exc)
        return response

    response.memory_references = [
        {
            "type": "automation_memory_event",
            "event_type": "TRIAGE_COMPLETED",
            "event_id": event.event_id,
        }
    ]
    return response


@app.post("/triage", response_model=TriageResponse)
def triage(payload: TriageRequest) -> TriageResponse:
    response = TriageResponse(**classify_exception(payload).model_dump())
    return record_triage_memory(payload, response)


@app.post(
    "/modernization/readiness",
    response_model=ModernizationReadinessResponse,
)
def modernization_readiness(
    payload: ModernizationReadinessRequest,
) -> ModernizationReadinessResponse:
    return run_readiness_agent(payload)


@app.post("/modernization/plan", response_model=ModernizationPlanResponse)
def modernization_plan(payload: ModernizationPlanRequest) -> ModernizationPlanResponse:
    return run_plan_agent(payload)
