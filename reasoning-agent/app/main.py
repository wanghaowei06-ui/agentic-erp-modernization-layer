from __future__ import annotations

import html as html_lib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Literal, TypedDict

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from schemas import TriageRequest as StableTriageRequest
from schemas import TriageResponse as StableTriageResponse
from shared.auth.api_key import require_memory_write_api_key
from shared.automation_memory.repository import count_repeated_gaps
from shared.automation_memory.repository import record_agent_decision
from triage import classify_exception

# Refactor hygiene: pure helpers and in-memory state extracted into
# submodules. The aliases below preserve the original private names used
# throughout this module so call sites do not need to change. The state
# objects (SIMULATION_QUEUE, APPROVAL_TASKS) are module-level singletons
# in their respective stores — importing them here binds the same dict
# object, so mutations remain visible across both modules.
from app.services.approval_store import APPROVAL_TASKS as _APPROVAL_TASKS
from app.services.approval_store import append_approval_event_to_run_memory as _append_approval_event_to_run_memory
from app.services.approval_store import append_erp_writeback_event_to_run_memory as _append_erp_writeback_event_to_run_memory
from app.services.approval_store import build_approvals_summary as _build_approvals_summary
from app.services.approval_store import generate_approval_id as _generate_approval_id
from app.services.approval_store import list_approvals as _list_approvals
from app.services.approval_store import pending_approval_count as _pending_approval_count
from app.services.simulation_store import SIMULATION_QUEUE as _SIMULATION_QUEUE
from app.services.simulation_store import build_simulation_summary as _build_simulation_summary
from app.services.simulation_store import claim_simulation_case
from app.services.simulation_store import find_simulation_case as _find_simulation_case
from app.services.simulation_store import reset_simulation_queue as _reset_simulation_queue
from app.services.simulation_store import simulation_state as _simulation_state
from app.ui.legacy_shell import render_legacy_shell as _render_legacy_shell

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

# PRD 17.5 Enhanced: LangGraph MemorySaver acts as the Case-level agent-state
# checkpoint layer. It persists the agent state for each case so that
# human-in-the-loop flows can resume context after an approval step, and so
# operators can inspect what the agent decided for a given case. It is NOT a
# substitute for the structured Automation Memory system of record (PRD 17.5
# "不推荐用途").
try:
    from langgraph.checkpoint.memory import MemorySaver

    _case_checkpointer: MemorySaver | None = None

    def case_checkpointer() -> MemorySaver:
        global _case_checkpointer
        if _case_checkpointer is None:
            _case_checkpointer = MemorySaver()
        return _case_checkpointer
except Exception:  # pragma: no cover - package availability depends on runtime env
    MemorySaver = None  # type: ignore[assignment]

    def case_checkpointer() -> None:
        return None


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


class CapabilityGapProposalRequest(BaseModel):
    """PRD 18.4 PO-1003 capability gap proposal input."""

    case_id: str = "CASE-003"
    po_id: str = "PO-1003"
    detected_exception_type: str = "inventory_shortage"
    required_business_action: str = "request_inventory_review"
    available_capabilities: list[str] = Field(
        default_factory=lambda: [
            "ReadPurchaseOrder.xaml",
            "request_purchase_order_approval_api",
        ]
    )


class CapabilityGapProposalResponse(BaseModel):
    """PRD 18.4 capability gap proposal output (exact field set)."""

    case_id: str
    coverage_status: str
    missing_capability: str
    recommended_next_step: str
    proposed_workflow_name: str
    human_approval_required: bool
    current_case_resolution: str
    # Supplementary fields from run_plan_agent (kept under separate keys so the
    # PRD 18.4 contract stays intact; downstream consumers may ignore these).
    plan_id: str | None = None
    target_tool_name: str | None = None
    proposed_endpoint: str | None = None
    contract_requirements: list[str] = []
    tests_required: list[str] = []


class CapabilityRegistryCheckRequest(BaseModel):
    """Input for the capability-registry check endpoint."""

    business_action: str
    exception_type: str | None = None
    case_id: str | None = None


class CapabilityRegistryCheckResponse(BaseModel):
    """Output of the capability-registry check.

    When a trusted capability is registered for the business action, the case
    can skip re-modernization and go straight to API/workflow execution. When
    none is found, the case is routed to capability-evolution evaluation.
    """

    capability_found: bool
    capability_id: str | None = None
    capability_type: str | None = None
    execution_mode: str | None = None
    modernization_required: bool
    next_stage: str


class CapabilityEvolutionEvaluateRequest(BaseModel):
    """Input for the capability-evolution evaluator."""

    case_id: str
    po_id: str | None = None
    exception_type: str
    business_action: str


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


class RouteAgentDecision(BaseModel):
    final_route: Literal[
        "STANDARD_PROCESSING",
        "WAITING_VENDOR_INFO",
        "CAPABILITY_GAP_DETECTED",
        "WAITING_MANUAL_INVESTIGATION",
        "WAITING_FOR_HUMAN_APPROVAL",
    ]
    policy_gate: Literal[
        "ALLOW_STANDARD_PROCESSING",
        "REQUIRE_HUMAN_APPROVAL",
        "WAIT_FOR_BUSINESS_DATA",
        "REQUIRE_CAPABILITY_REVIEW",
        "REQUIRE_MANUAL_INVESTIGATION",
    ]
    explanation: str
    confidence: float = Field(ge=0, le=1)
    context_signals_used: list[str] = []


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
        "enterprise_context_source": "mock_enterprise_context",
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
    if agent_name == "route":
        context = json.loads(prompt.split("ROUTE_CONTEXT_JSON:", 1)[1].strip())
        order = context.get("order", {})
        company = context.get("company_context", {}).get("company", {})
        exception_text = str(order.get("raw_exception_text") or "").lower()
        business_remarks = str(order.get("business_remarks") or "").lower()
        if order.get("amount", 0) > order.get("budget_limit", 0):
            return {
                "final_route": "WAITING_FOR_HUMAN_APPROVAL",
                "policy_gate": "REQUIRE_HUMAN_APPROVAL",
                "explanation": (
                    "Budget is exceeded. Finance policy requires manager approval above "
                    "budget, while Q4 strategic-account impact in the buyer note makes this "
                    "a business approval decision rather than an automatic rejection."
                ),
                "confidence": 0.91,
                "context_signals_used": [
                    "finance_policy.requires_manager_approval_above_budget",
                    "sales_context.quarter_end_revenue_pressure",
                    "business_remarks",
                ],
            }
        if (
            not order.get("vendor_info_complete", True)
            or not str(order.get("vendor_id") or "").strip()
            or "vendor" in exception_text
        ):
            return {
                "final_route": "WAITING_VENDOR_INFO",
                "policy_gate": "WAIT_FOR_BUSINESS_DATA",
                "explanation": (
                    "Vendor compliance data is missing. Strict vendor compliance in company "
                    "policy means the ERP case should wait for vendor master data before "
                    "processing."
                ),
                "confidence": 0.9,
                "context_signals_used": [
                    "finance_policy.strict_vendor_compliance",
                    "business_remarks",
                ],
            }
        if not order.get("inventory_available", True) or "inventory" in exception_text:
            return {
                "final_route": "CAPABILITY_GAP_DETECTED",
                "policy_gate": "REQUIRE_CAPABILITY_REVIEW",
                "explanation": (
                    "Inventory shortage intersects with low operations risk tolerance and "
                    "substitute-part review requirements, so the case should be flagged as "
                    "a capability gap for supply-chain review."
                ),
                "confidence": 0.88,
                "context_signals_used": [
                    "operations_context.inventory_risk_tolerance",
                    "operations_context.substitute_part_review_required",
                    "business_remarks",
                ],
            }
        if "incomplete" in business_remarks or "attention" in exception_text:
            return {
                "final_route": "WAITING_MANUAL_INVESTIGATION",
                "policy_gate": "REQUIRE_MANUAL_INVESTIGATION",
                "explanation": (
                    "The business justification is incomplete even though the order may support "
                    "revenue. Manual investigation is required before execution."
                ),
                "confidence": 0.78,
                "context_signals_used": [
                    "sales_context.quarter_end_revenue_pressure",
                    "business_remarks",
                ],
            }
        return {
            "final_route": "WAITING_MANUAL_INVESTIGATION",
            "policy_gate": "REQUIRE_MANUAL_INVESTIGATION",
            "explanation": (
                f"Company context for {company.get('name', 'the company')} was reviewed, "
                "but the order did not match a safe automatic exception route."
            ),
            "confidence": 0.72,
            "context_signals_used": ["company_context", "business_remarks"],
        }
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


def company_context_payload() -> dict[str, Any]:
    return {
        "enterprise_context_source": "mock_enterprise_context",
        "enterprise_context_mode": "local_demo_snapshot",
        "enterprise_context_provider": "reasoning-agent.company_context_payload",
        "company": {
            "name": "Demo Manufacturing Group",
            "current_quarter": "Q4",
            "strategic_goals": [
                "protect strategic customer renewals",
                "reduce uncontrolled procurement spend",
                "avoid production stoppage for critical orders",
            ],
            "finance_policy": {
                "budget_exception_threshold": 10000,
                "requires_manager_approval_above_budget": True,
                "strict_vendor_compliance": True,
            },
            "sales_context": {
                "strategic_accounts_at_risk": ["ACME Retail", "Northwind Energy"],
                "quarter_end_revenue_pressure": True,
            },
            "operations_context": {
                "inventory_risk_tolerance": "low",
                "substitute_part_review_required": True,
            },
        }
    }


@app.get("/company-context")
def company_context() -> dict[str, Any]:
    return company_context_payload()


@app.post("/company-context/lookup")
def company_context_lookup() -> dict[str, Any]:
    return company_context_payload()


def _company_context_reference_for_route(final_route: str) -> dict[str, bool]:
    return {
        "finance_policy_used": True,
        "sales_context_used": True,
        "operations_context_used": True,
    }


def build_route_prompt(
    *,
    payload: TriageRequest,
    precheck_result: dict[str, Any],
    triage_result: dict[str, Any] | None,
    company_context: dict[str, Any],
) -> str:
    route_context = {
        "instruction": (
            "Decide the ERP route using the purchase order, ERP system message, "
            "business remarks, and company context. Return final_route, policy_gate, "
            "explanation, confidence, and context_signals_used."
        ),
        "allowed_final_routes": [
            "STANDARD_PROCESSING",
            "WAITING_VENDOR_INFO",
            "CAPABILITY_GAP_DETECTED",
            "WAITING_MANUAL_INVESTIGATION",
            "WAITING_FOR_HUMAN_APPROVAL",
        ],
        "allowed_policy_gate": [
            "ALLOW_STANDARD_PROCESSING",
            "REQUIRE_HUMAN_APPROVAL",
            "WAIT_FOR_BUSINESS_DATA",
            "REQUIRE_CAPABILITY_REVIEW",
            "REQUIRE_MANUAL_INVESTIGATION",
        ],
        "order": payload.model_dump(),
        "precheck": precheck_result,
        "triage_result": triage_result,
        "company_context": company_context,
        "enterprise_context_source": company_context.get(
            "enterprise_context_source", "mock_enterprise_context"
        ),
    }
    return "ROUTE_CONTEXT_JSON:" + json.dumps(route_context, ensure_ascii=False)


def _route_agent_fail_closed(
    *,
    payload: TriageRequest,
    config: LlmConfig,
    fallback_route: str,
    fallback_policy: str,
    reason: str,
    evidence: LlmCallEvidence | None = None,
) -> dict[str, Any]:
    proof = metadata(
        config=config,
        llm_enabled=bool(config.api_key or config.demo_mode == "mock_success"),
        schema_validated=False,
        guardrails_applied=True,
        decision_status="MANUAL_REVIEW_REQUIRED",
        evidence=evidence,
        invocation_verified=False,
    )
    return {
        "final_route": fallback_route,
        "policy_gate_decision": fallback_policy,
        "agent_reasoning_summary": (
            "LLM-backed route agent could not produce a validated enterprise-context "
            f"decision for {payload.po_id}. {reason}"
        ),
        "agent_context_used": True,
        "company_context_reference": {
            "finance_policy_used": True,
            "sales_context_used": True,
            "operations_context_used": True,
        },
        "llm_validation_proof": proof,
    }


def run_route_agent(
    *,
    payload: TriageRequest,
    precheck_result: dict[str, Any],
    triage_result: dict[str, Any] | None,
    company_context: dict[str, Any],
    fallback_route: str,
    fallback_policy: str,
) -> dict[str, Any]:
    config = load_llm_config()
    if not llm_available(config):
        return _route_agent_fail_closed(
            payload=payload,
            config=config,
            fallback_route=fallback_route,
            fallback_policy=fallback_policy,
            reason="MODEL_UNAVAILABLE",
        )
    prompt = build_route_prompt(
        payload=payload,
        precheck_result=precheck_result,
        triage_result=triage_result,
        company_context=company_context,
    )
    try:
        result = call_llm_structured(prompt, config, "route", RouteAgentDecision)
        decision: RouteAgentDecision = result.value
    except RuntimeError as exc:
        return _route_agent_fail_closed(
            payload=payload,
            config=config,
            fallback_route=fallback_route,
            fallback_policy=fallback_policy,
            reason=str(exc),
        )

    proof = metadata(
        config=config,
        llm_enabled=True,
        schema_validated=True,
        guardrails_applied=True,
        decision_status="DECISION_READY",
        evidence=result.evidence,
        invocation_verified=True,
    )
    context_ref = _company_context_reference_for_route(decision.final_route)
    # LLM may mention only one domain, but the route prompt always includes the
    # complete context. Mark the relevant route family as used while preserving
    # explicit proof that company context was consulted.
    if "company_context" in decision.context_signals_used:
        context_ref = {
            "finance_policy_used": True,
            "sales_context_used": True,
            "operations_context_used": True,
        }
    return {
        "final_route": decision.final_route,
        "policy_gate_decision": decision.policy_gate,
        "agent_reasoning_summary": decision.explanation,
        "agent_context_used": True,
        "company_context_reference": context_ref,
        "agent_context_signals_used": decision.context_signals_used,
        "agent_confidence": decision.confidence,
        "llm_validation_proof": proof,
    }


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
        thread_id=payload.case_id,
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
        thread_id=payload.case_id,
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
        thread_id=payload.case_id,
    )["response"]


# PRD 18.2: Capability Evolution Loop trigger. When the number of repeated
# capability gaps for an uncovered business action reaches a configurable
# threshold, the system automatically proposes a new capability by invoking
# run_plan_agent. Every proposal still requires human review, validation and
# registration before reuse (PRD 18.5 safety boundary).
CAPABILITY_EVOLUTION_THRESHOLD_ENV = "CAPABILITY_EVOLUTION_THRESHOLD"
DEFAULT_CAPABILITY_EVOLUTION_THRESHOLD = 3

# Threshold for proposal generation from real Run Memory patterns.
# When a real pattern's observed_count < PROPOSAL_THRESHOLD, the evaluator
# returns KEEP_ACCUMULATING_EVIDENCE instead of generating a proposal.
PROPOSAL_THRESHOLD_ENV = "PROPOSAL_THRESHOLD"
DEFAULT_PROPOSAL_THRESHOLD = 3


def proposal_threshold() -> int:
    """Return the minimum observed_count for a real pattern to generate a proposal."""
    raw = os.getenv(PROPOSAL_THRESHOLD_ENV)
    if raw is None:
        return DEFAULT_PROPOSAL_THRESHOLD
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PROPOSAL_THRESHOLD
    return max(1, value)


def capability_evolution_threshold() -> int:
    raw = os.getenv(CAPABILITY_EVOLUTION_THRESHOLD_ENV)
    if raw is None:
        return DEFAULT_CAPABILITY_EVOLUTION_THRESHOLD
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "[CapabilityEvolution] invalid %s=%r, using default %d",
            CAPABILITY_EVOLUTION_THRESHOLD_ENV,
            raw,
            DEFAULT_CAPABILITY_EVOLUTION_THRESHOLD,
        )
        return DEFAULT_CAPABILITY_EVOLUTION_THRESHOLD
    return max(1, value)


def maybe_trigger_capability_evolution(
    business_action: str,
    case_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate the capability-evolution trigger for ``business_action``.

    Returns a dict describing whether the trigger fired and, when it did, the
    generated modernization plan. Failures during plan generation are logged
    and contained so callers (e.g. a gap-recording endpoint) are never blocked.
    """
    threshold = capability_evolution_threshold()
    repeated_gaps = count_repeated_gaps(business_action)
    logger.info(
        "[CapabilityEvolution] business_action=%s repeated_gaps=%d threshold=%d",
        business_action,
        repeated_gaps,
        threshold,
    )
    if repeated_gaps < threshold:
        return {
            "triggered": False,
            "business_action": business_action,
            "repeated_gaps": repeated_gaps,
            "threshold": threshold,
            "plan": None,
        }

    logger.info(
        "[CapabilityEvolution] threshold reached for business_action=%s "
        "(repeated_gaps=%d); invoking run_plan_agent",
        business_action,
        repeated_gaps,
    )
    request = ModernizationPlanRequest(
        case_id=case_id or f"CAPABILITY_EVOLUTION_{business_action}",
        business_action=business_action,
        modernization_candidate=True,
        recommended_api_tool_name=business_action,
    )
    try:
        plan = run_plan_agent(request)
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning(
            "[CapabilityEvolution] run_plan_agent failed for "
            "business_action=%s: %s",
            business_action,
            exc,
        )
        return {
            "triggered": True,
            "business_action": business_action,
            "repeated_gaps": repeated_gaps,
            "threshold": threshold,
            "plan": None,
            "error": str(exc),
        }
    logger.info(
        "[CapabilityEvolution] plan generated plan_id=%s target_tool=%s "
        "recommended_next_stage=%s",
        plan.plan_id,
        plan.target_tool_name,
        plan.recommended_next_stage,
    )
    return {
        "triggered": True,
        "business_action": business_action,
        "repeated_gaps": repeated_gaps,
        "threshold": threshold,
        "plan": plan,
    }


def invoke_graph(
    state_type: type[AgentState],
    nodes: list[tuple[str, Any]],
    initial_state: AgentState,
    *,
    thread_id: str | None = None,
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
    # PRD 17.5 Enhanced: when a thread_id (case_id) is supplied, compile with
    # the MemorySaver checkpointer so the agent state is persisted per case and
    # can be resumed after a human-in-the-loop step.
    checkpointer = case_checkpointer() if thread_id else None
    compiled = graph.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}} if thread_id else None
    return compiled.invoke(initial_state, config=config)


def _to_pascal_case(value: str) -> str:
    """Convert ``snake_case`` / ``kebab-case`` to ``PascalCase``."""
    parts = [p for p in value.replace("-", "_").split("_") if p]
    return "".join(part[:1].upper() + part[1:].lower() for part in parts)


def derive_proposed_workflow_name(
    exception_type: str,
    business_action: str,
) -> str:
    """Derive a PRD 18.4 ``proposed_workflow_name`` from the gap inputs.

    The convention follows the PRD 18.4 example: for ``inventory_shortage`` +
    ``request_inventory_review`` the proposed workflow is
    ``HandleInventoryShortageReview.xaml``. ``Handle`` prefixes the workflow
    (it is a UiPath automation handler), the exception type is PascalCased, and
    ``Review`` is appended when the business action names a review step.
    """
    suffix = "Review" if "review" in business_action.lower() else ""
    return f"Handle{_to_pascal_case(exception_type)}{suffix}.xaml"


def generate_capability_gap_proposal(
    payload: CapabilityGapProposalRequest,
) -> CapabilityGapProposalResponse:
    """Generate a PRD 18.4 capability gap proposal for PO-1003.

    Reuses ``run_plan_agent`` to produce the modernization plan; the plan's
    ``target_tool_name`` and the gap's ``detected_exception_type`` are used to
    derive the PRD-mandated ``proposed_workflow_name``. The proposal is a
    recommendation only and always requires human approval (PRD 18.5).
    """
    plan_request = ModernizationPlanRequest(
        case_id=payload.case_id,
        business_action=payload.required_business_action,
        modernization_candidate=True,
        recommended_api_tool_name=payload.required_business_action,
    )
    plan = run_plan_agent(plan_request)

    proposed_workflow_name = derive_proposed_workflow_name(
        payload.detected_exception_type,
        payload.required_business_action,
    )

    return CapabilityGapProposalResponse(
        case_id=payload.case_id,
        coverage_status="not_covered",
        missing_capability=(
            "No registered workflow or API tool can handle "
            f"{payload.detected_exception_type.replace('_', ' ')} review."
        ),
        recommended_next_step="create_new_workflow_proposal",
        proposed_workflow_name=proposed_workflow_name,
        human_approval_required=True,
        current_case_resolution="manual_handling_required",
        plan_id=plan.plan_id,
        target_tool_name=plan.target_tool_name,
        proposed_endpoint=plan.proposed_endpoint,
        contract_requirements=plan.contract_requirements,
        tests_required=plan.tests_required,
    )


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

    response.memory_references.append(
        {
            "type": "automation_memory_event",
            "event_type": "TRIAGE_COMPLETED",
            "event_id": event.event_id,
        }
    )
    return response


@app.post("/precheck")
def precheck(payload: TriageRequest) -> dict[str, Any]:
    """Deterministic pre-check router that classifies a purchase order into one
    of three routes before any agent is invoked.

    A. NORMAL — standard processing, no agent required.
    B. CLEAR_EXCEPTION — explicit deterministic exception, route to triage agent.
    C. AMBIGUOUS — fields missing / conflicting / fuzzy text, route to semantic agent.

    This router is intentionally deterministic and side-effect free: it does
    not write memory, does not call an LLM, and does not modify XAML or deploy
    APIs. Its sole job is to pick the next route.
    """
    amount = payload.amount
    budget_limit = payload.budget_limit
    vendor_info_complete = payload.vendor_info_complete
    inventory_available = payload.inventory_available
    erp_status = (payload.erp_status or "").strip()
    raw_text = (payload.raw_exception_text or "").strip()

    # --- AMBIGUOUS: missing/conflicting fields -------------------------------
    # amount/budget missing or non-numeric is impossible (Pydantic validates),
    # but conflict (amount < 0, budget < 0) or unknown erp_status with text
    # indicating exception is ambiguous.
    erp_lower = erp_status.lower()
    known_normal = {"normal", "ready", "open", ""}
    known_exception = {"exception", "error", "failed", "blocked"}
    text_hints_exception = any(
        kw in raw_text.lower()
        for kw in ("exception", "exceeds", "missing", "shortage", "error", "blocked")
    )

    # Ambiguous: erp_status is neither known-normal nor known-exception, AND
    # there's conflicting text.
    if erp_lower not in known_normal and erp_lower not in known_exception:
        if text_hints_exception or raw_text:
            return {
                "case_id": payload.case_id,
                "po_id": payload.po_id,
                "precheck_result": "AMBIGUOUS",
                "case_type": "ambiguous",
                "route": "AGENT_SEMANTIC_ROUTING",
                "agent_required": True,
                "exception_detected": "uncertain",
                "recommended_next": "AGENT_SEMANTIC_ROUTING",
                "next_stage": "AGENT_SEMANTIC_ROUTING",
                "reason": (
                    "ERP status and raw exception text are insufficient to "
                    "determine a normal or clear-exception outcome; semantic "
                    "agent routing required to disambiguate."
                ),
                "signals": {
                    "erp_status": erp_status,
                    "raw_exception_text": raw_text,
                },
            }

    # Ambiguous: erp_status says Exception but no field-level exception is
    # actually present (amount ok, vendor ok, inventory ok) and no text hint.
    erp_says_exception = erp_lower in known_exception
    field_level_exception = (
        amount > budget_limit
        or not vendor_info_complete
        or not inventory_available
    )
    if erp_says_exception and not field_level_exception and not text_hints_exception:
        return {
            "case_id": payload.case_id,
            "po_id": payload.po_id,
            "precheck_result": "AMBIGUOUS",
            "case_type": "ambiguous",
            "route": "AGENT_SEMANTIC_ROUTING",
            "agent_required": True,
            "exception_detected": "uncertain",
            "recommended_next": "AGENT_SEMANTIC_ROUTING",
            "next_stage": "AGENT_SEMANTIC_ROUTING",
            "reason": (
                "ERP status is 'Exception' but no deterministic field-level "
                "exception is present; semantic agent routing required."
            ),
            "signals": {
                "erp_status": erp_status,
                "amount_within_budget": amount <= budget_limit,
                "vendor_info_complete": vendor_info_complete,
                "inventory_available": inventory_available,
            },
        }

    # --- CLEAR_EXCEPTION: explicit deterministic exception -------------------
    if (
        amount > budget_limit
        or not vendor_info_complete
        or not inventory_available
        or erp_says_exception
    ):
        # Derive a hint of which exception for the next-stage recommendation.
        if amount > budget_limit:
            hint = "budget_exceeded"
        elif not vendor_info_complete:
            hint = "vendor_info_missing"
        elif not inventory_available:
            hint = "inventory_shortage"
        else:
            hint = "erp_exception"
        return {
            "case_id": payload.case_id,
            "po_id": payload.po_id,
            "precheck_result": "CLEAR_EXCEPTION",
            "case_type": "exception",
            "route": "CALL_TRIAGE_AGENT",
            "agent_required": True,
            "exception_detected": True,
            "exception_hint": hint,
            "recommended_next": "CALL_TRIAGE_AGENT",
            "next_stage": "WAITING_FOR_TRIAGE",
            "reason": (
                f"Deterministic exception detected ({hint}); route to triage agent "
                "for classification and capability lookup."
            ),
            "signals": {
                "amount": amount,
                "budget_limit": budget_limit,
                "amount_exceeds_budget": amount > budget_limit,
                "vendor_info_complete": vendor_info_complete,
                "inventory_available": inventory_available,
                "erp_status": erp_status,
            },
        }

    # --- NORMAL: standard processing -----------------------------------------
    # At this point: amount <= budget, vendor complete, inventory available,
    # erp_status is a known-normal value, no exception text.
    return {
        "case_id": payload.case_id,
        "po_id": payload.po_id,
        "precheck_result": "NORMAL",
        "case_type": "normal",
        "route": "STANDARD_PROCESSING",
        "agent_required": False,
        "exception_detected": False,
        "recommended_next": "STANDARD_PROCESSING",
        "next_stage": "STANDARD_PROCESSING",
        "reason": (
            "Purchase order passes all deterministic precheck rules: amount "
            "within budget, vendor information complete, inventory available, "
            "ERP status normal, and no exception text."
        ),
        "signals": {
            "amount": amount,
            "budget_limit": budget_limit,
            "vendor_info_complete": vendor_info_complete,
            "inventory_available": inventory_available,
            "erp_status": erp_status,
        },
    }


@app.post("/triage", response_model=TriageResponse)
def triage(
    payload: TriageRequest,
    _api_key: str | None = Depends(require_memory_write_api_key),
) -> TriageResponse:
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


class CapabilityEvolutionRequest(BaseModel):
    business_action: str
    case_id: str | None = None


class CapabilityEvolutionResponse(BaseModel):
    triggered: bool
    business_action: str
    repeated_gaps: int
    threshold: int
    plan: ModernizationPlanResponse | None = None
    error: str | None = None


@app.post(
    "/capability-evolution/evaluate",
    response_model=CapabilityEvolutionResponse,
    response_model_exclude_none=True,
)
def capability_evolution_evaluate(
    payload: CapabilityEvolutionRequest,
) -> CapabilityEvolutionResponse:
    """Evaluate the Capability Evolution Loop trigger (PRD 18.2).

    When ``count_repeated_gaps`` for the requested business action reaches the
    configured threshold, ``run_plan_agent`` is invoked to generate a
    modernization proposal. The proposal is a recommendation only; it still
    requires human review, validation and registration (PRD 18.5).
    """
    result = maybe_trigger_capability_evolution(
        payload.business_action,
        case_id=payload.case_id,
    )
    return CapabilityEvolutionResponse(**result)


@app.post(
    "/capability-gap/proposal",
    response_model=CapabilityGapProposalResponse,
    response_model_exclude_none=True,
)
def capability_gap_proposal(
    payload: CapabilityGapProposalRequest,
) -> CapabilityGapProposalResponse:
    """Generate a PRD 18.4 capability gap proposal for PO-1003.

    Reuses ``run_plan_agent`` to draft the modernization plan and derives the
    ``proposed_workflow_name``. The proposal is a recommendation only; it
    requires human approval, validation and registration before reuse
    (PRD 18.5).
    """
    return generate_capability_gap_proposal(payload)


@app.get("/agent-state/{case_id}")
def get_case_agent_state(case_id: str) -> dict[str, Any]:
    """Retrieve the LangGraph checkpoint state for a case (PRD 17.5 Enhanced).

    Returns the persisted agent state (latest checkpoint) for the given case,
    or ``404`` if no checkpoint exists. This supports human-in-the-loop context
    recovery: after an approval step, an operator can inspect what the agent
    decided for the case. The checkpoint is an in-memory, case-scoped agent
    state; it is NOT the structured Automation Memory system of record.
    """
    if not LANGGRAPH_AVAILABLE or MemorySaver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "checkpoint_unavailable",
                "message": "LangGraph MemorySaver is not available in this runtime.",
            },
        )
    checkpointer = case_checkpointer()
    config = {"configurable": {"thread_id": case_id}}
    try:
        checkpoint_tuple = checkpointer.get_tuple(config)
    except Exception:  # pragma: no cover - defensive guard
        checkpoint_tuple = None
    if checkpoint_tuple is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "no_checkpoint",
                "message": f"No agent-state checkpoint found for case {case_id}.",
            },
        )
    checkpoint = checkpoint_tuple.checkpoint
    channel_values = checkpoint.get("channel_values", {})
    # ``channel_values`` may contain non-serialisable objects (e.g. pydantic
    # models); fall back to a safe representation.
    safe_state: dict[str, Any] = {}
    for key, value in channel_values.items():
        if hasattr(value, "model_dump"):
            safe_state[key] = value.model_dump()
        elif isinstance(value, (str, int, float, bool, list, dict, type(None))):
            safe_state[key] = value
        else:
            safe_state[key] = str(value)
    return {
        "case_id": case_id,
        "thread_id": case_id,
        "checkpoint_id": checkpoint.get("id"),
        "parent_checkpoint_id": checkpoint.get("parent_config"),
        "agent_state": safe_state,
    }


@app.post(
    "/capability-registry/check",
    response_model=CapabilityRegistryCheckResponse,
    response_model_exclude_none=True,
)
def capability_registry_check(
    payload: CapabilityRegistryCheckRequest,
) -> CapabilityRegistryCheckResponse:
    """Check whether a trusted capability already covers a business action.

    Behaviour:
    - If ``capability_registry.json`` has a trusted API tool or workflow for the
      given ``business_action``, the case can skip re-modernization and proceed
      directly to execution (API mode or workflow mode).
    - Otherwise the case is routed to capability-evolution evaluation so a
      proposal (not an auto-deployment) can be generated.

    This lets PO-1001 go through API modernization once, and on subsequent runs
    show "trusted API already registered" and enter API mode without repeating
    the modernization flow.
    """
    from memory.repository import find_trusted_capability

    capability = find_trusted_capability(payload.business_action)
    if capability and capability.get("status") == "trusted":
        return CapabilityRegistryCheckResponse(
            capability_found=True,
            capability_id=capability.get("capability_id"),
            capability_type=capability.get("type"),
            execution_mode=capability.get("execution_mode"),
            modernization_required=False,
            next_stage=(
                "API_MODE_EXECUTION"
                if capability.get("execution_mode") == "API"
                else "WORKFLOW_MODE_EXECUTION"
            ),
        )

    return CapabilityRegistryCheckResponse(
        capability_found=False,
        modernization_required=True,
        next_stage="CAPABILITY_EVOLUTION_EVALUATION",
    )


def _load_historical_pattern(exception_type: str, business_action: str) -> dict[str, Any]:
    """Load the matching historical pattern for an exception/action pair."""
    from memory.store import read_json

    patterns = read_json("historical_patterns.json", [])
    for pattern in patterns:
        if (
            pattern.get("exception_type") == exception_type
            and pattern.get("business_action") == business_action
        ):
            return pattern
    return {}


def _load_validation_result(case_id: str) -> dict[str, Any]:
    from memory.store import read_json

    return read_json(f"validation_result_{case_id}.json", {})


def _load_capability_gap(case_id: str) -> dict[str, Any]:
    from memory.store import read_json

    return read_json(f"capability_gap_{case_id}.json", {})


def _build_evolution_explainability(
    *,
    decision_label: str,
    case_id: str,
    business_action: str,
    exception_type: str,
    pattern: dict[str, Any],
    observed_count: int,
    business_value: float,
    field_stability: float,
    validation_pass_rate: float,
    validation_passed_now: bool,
    selector_failure_count: int,
    repeated_gap_count: int,
    trusted_capability_exists: bool,
    no_api_modernization: bool,
) -> dict[str, Any]:
    """Build the explainability fields for a capability-evolution decision.

    Returns ``rule_evaluation``, ``evidence``, ``pattern_snapshot`` and
    ``why_not`` so callers can understand *why* the decision was chosen and
    *why not* alternative decisions. All fields are additive — they never
    replace existing decision fields, preserving backward compatibility.
    """
    process_signature = (
        f"{business_action}__{exception_type}"
        if business_action and exception_type
        else ""
    )

    # Load the real-run pattern file for evidence_run_ids (falls back to []
    # when no real run has been committed yet).
    evidence_run_ids: list[str] = []
    pattern_file = (
        f"memory/patterns/{process_signature}.json" if process_signature else None
    )
    if process_signature:
        try:
            from memory.patterns import load_pattern
            real_pattern = load_pattern(process_signature)
            if real_pattern:
                evidence_run_ids = list(real_pattern.get("latest_run_ids", []))
        except Exception:
            pass

    # Look up the latest run_id for the case to construct validation_file path.
    validation_file: str | None = None
    try:
        from memory.run_memory import case_dir
        latest_run_path = case_dir(case_id) / "latest_run_id.txt"
        if latest_run_path.exists():
            latest_run_id = latest_run_path.read_text(encoding="utf-8").strip()
            if latest_run_id:
                validation_file = (
                    f"memory/runs/{latest_run_id}/raw/validation_response.json"
                )
    except Exception:
        pass

    rule_evaluation = {
        "observed_count >= 5": observed_count >= 5,
        "business_value >= 0.75": business_value >= 0.75,
        "field_stability >= 0.75": field_stability >= 0.75,
        "validation_pass_rate >= 0.8": validation_pass_rate >= 0.8,
        "validation_passed_now": validation_passed_now,
        "human_approval_gate_exists": True,
        "trusted_capability_exists": trusted_capability_exists,
        "repeated_gap_count >= 3": repeated_gap_count >= 3,
        "selector_failure_count >= 3": selector_failure_count >= 3,
        "no_api_modernization_required": no_api_modernization,
    }

    evidence = {
        "process_signature": process_signature,
        "evidence_run_ids": evidence_run_ids,
        "pattern_file": pattern_file,
        "validation_file": validation_file,
        "repeated_gap_count": repeated_gap_count,
    }

    pattern_snapshot = {
        "observed_count": observed_count,
        "validation_pass_count": pattern.get("validation_pass_count", 0),
        "validation_pass_rate": round(validation_pass_rate, 4),
        "field_stability": field_stability,
        "business_value": business_value,
        "ui_fragility": pattern.get("ui_fragility", 0.0),
        "selector_failure_count": selector_failure_count,
    }

    why_not: dict[str, str] = {}
    if decision_label == "USE_TRUSTED_CAPABILITY":
        why_not["API_MODERNIZATION_PROPOSAL"] = (
            "A trusted API capability is already registered; no modernization needed."
        )
        why_not["KEEP_RPA_MODE"] = (
            "Trusted API capability is registered and ready for reuse."
        )
    elif decision_label == "API_MODERNIZATION_PROPOSAL":
        why_not["XAML_WORKFLOW_PROPOSAL"] = (
            "Existing action is stable and API-ready; a new XAML workflow is unnecessary."
        )
        why_not["KEEP_RPA_MODE"] = (
            "Validation and parity evidence are sufficient to propose modernization."
        )
    elif decision_label == "XAML_WORKFLOW_PROPOSAL":
        why_not["API_MODERNIZATION_PROPOSAL"] = (
            "Field stability or validation evidence is insufficient for API modernization; "
            "a new XAML workflow is more appropriate."
        )
        why_not["KEEP_RPA_MODE"] = (
            "Repeated gap detected; keeping RPA mode without a workflow proposal would not close the coverage gap."
        )
    elif decision_label == "XAML_IMPROVEMENT_PROPOSAL":
        why_not["API_MODERNIZATION_PROPOSAL"] = (
            "Selector fragility indicates the existing workflow needs hardening before API modernization can be considered."
        )
        why_not["XAML_WORKFLOW_PROPOSAL"] = (
            "An existing workflow already covers this action; it needs improvement, not replacement."
        )
    elif decision_label == "WAIT_FOR_VENDOR_INFO":
        why_not["API_MODERNIZATION_PROPOSAL"] = (
            "Business data is missing (vendor info); this is not an automation problem."
        )
    elif decision_label == "KEEP_RPA_MODE":
        why_not["API_MODERNIZATION_PROPOSAL"] = (
            "Insufficient evidence for modernization (stability, validation, or business value below threshold)."
        )
    elif decision_label == "NO_EVOLUTION_REQUIRED":
        why_not["API_MODERNIZATION_PROPOSAL"] = (
            "Normal standard path — no exception detected, no modernization needed."
        )
        why_not["XAML_WORKFLOW_PROPOSAL"] = (
            "Normal standard path — no capability gap to close with a new workflow."
        )
        why_not["KEEP_RPA_MODE"] = (
            "Normal standard path is already optimal; no RPA fragility to investigate."
        )
    elif decision_label == "MANUAL_INVESTIGATION":
        why_not["API_MODERNIZATION_PROPOSAL"] = (
            "Ambiguous case — insufficient evidence and low confidence; cannot "
            "justify modernization without human investigation."
        )
        why_not["XAML_WORKFLOW_PROPOSAL"] = (
            "Ambiguous case — no confirmed capability gap; cannot propose a new "
            "workflow until the business state is clarified."
        )
        why_not["NO_EVOLUTION_REQUIRED"] = (
            "Ambiguous case — not a normal standard path; exception state is "
            "uncertain and requires human review."
        )
    elif decision_label == "KEEP_ACCUMULATING_EVIDENCE":
        why_not["API_MODERNIZATION_PROPOSAL"] = (
            "Insufficient real-run observations; the pattern has not reached "
            "the proposal threshold yet."
        )
        why_not["XAML_WORKFLOW_PROPOSAL"] = (
            "Insufficient real-run observations; the pattern has not reached "
            "the proposal threshold yet."
        )
        why_not["KEEP_RPA_MODE"] = (
            "Evidence is still accumulating; no decision to change RPA mode yet."
        )

    return {
        "rule_evaluation": rule_evaluation,
        "evidence": evidence,
        "pattern_snapshot": pattern_snapshot,
        "why_not": why_not,
    }


def evaluate_capability_evolution(
    payload: CapabilityEvolutionEvaluateRequest,
) -> dict[str, Any]:
    """Evaluate the capability-evolution route for a case.

    Decision tree (mutually exclusive, in priority order):
    1. USE_TRUSTED_CAPABILITY — registry already has a trusted capability.
    2. API_MODERNIZATION_PROPOSAL — repeated, high-value, stable action with
       passed validation.
    3. XAML_WORKFLOW_PROPOSAL / XAML_IMPROVEMENT_PROPOSAL — repeated gap with no
       trusted coverage, or selector fragility.
    4. KEEP_RPA_MODE — validation failed / unstable / unclear business rule.

    The decision is persisted to
    ``memory/data/capability_evolution_decision_{case_id}.json``. This endpoint
    only creates proposals; it never auto-deploys APIs or modifies XAML.

    Backward-compatible explainability fields (``rule_evaluation``,
    ``evidence``, ``pattern_snapshot``, ``why_not``) are added to every
    decision so callers can understand *why* the decision was chosen.
    """
    from memory.repository import (
        find_trusted_capability,
        record_capability_evolution_decision,
    )

    case_id = payload.case_id
    business_action = payload.business_action
    exception_type = payload.exception_type

    legacy_pattern = _load_historical_pattern(exception_type, business_action)
    validation = _load_validation_result(case_id)
    gap = _load_capability_gap(case_id)

    # Load the real-run pattern file (memory/patterns/{process_signature}.json).
    # When a real pattern exists, prefer it over the legacy seed data so that
    # threshold-based proposal logic uses actual run-memory observations.
    process_signature = (
        f"{business_action}__{exception_type}"
        if business_action and exception_type
        else ""
    )
    real_pattern: dict[str, Any] = {}
    if process_signature:
        try:
            from memory.patterns import load_pattern as _load_real_pattern
            real_pattern = _load_real_pattern(process_signature) or {}
        except Exception:
            real_pattern = {}
    if not real_pattern or real_pattern.get("source") != "real_run_memory":
        try:
            from memory.patterns import list_patterns as _list_real_patterns
            matching_patterns = [
                p for p in _list_real_patterns()
                if p.get("source") == "real_run_memory"
                and p.get("business_action") == business_action
                and p.get("exception_type") == exception_type
            ]
            if matching_patterns:
                real_pattern = max(
                    matching_patterns,
                    key=lambda p: int(p.get("observed_count", 0) or 0),
                )
        except Exception:
            pass

    # Only prefer real_pattern when it's an actual real-run pattern file
    # (source="real_run_memory"). When load_pattern returns a seed
    # (source="seed_historical_memory"), use the legacy pattern instead —
    # the legacy raw entry carries fields (no_api_modernization_required,
    # repeated_gap_count) that the legacy multi-condition logic depends on.
    if real_pattern and real_pattern.get("source") == "real_run_memory":
        pattern = real_pattern
    else:
        pattern = legacy_pattern

    # Compute all signals upfront so rule_evaluation is available for every
    # decision branch (including USE_TRUSTED_CAPABILITY).
    capability = find_trusted_capability(business_action)
    trusted_capability_exists = bool(
        capability and capability.get("status") == "trusted"
    )
    observed_count = pattern.get("observed_count", 0)
    business_value = pattern.get("business_value", 0.0)
    field_stability = pattern.get("field_stability", 0.0)
    validation_pass_count = pattern.get("validation_pass_count", 0)
    validation_pass_rate = (
        validation_pass_count / observed_count if observed_count else 0.0
    )
    validation_passed_now = (
        validation.get("contract_test") == "passed"
        and validation.get("business_rule_test") == "passed"
    )
    selector_failure_count = pattern.get("selector_failure_count", 0)
    repeated_gap_count = pattern.get("repeated_gap_count", 0)
    trusted_capability_found = pattern.get("trusted_capability_found", False)
    no_api_modernization = pattern.get("no_api_modernization_required", False)

    explain_kwargs = dict(
        case_id=case_id,
        business_action=business_action,
        exception_type=exception_type,
        pattern=pattern,
        observed_count=observed_count,
        business_value=business_value,
        field_stability=field_stability,
        validation_pass_rate=validation_pass_rate,
        validation_passed_now=validation_passed_now,
        selector_failure_count=selector_failure_count,
        repeated_gap_count=repeated_gap_count,
        trusted_capability_exists=trusted_capability_exists,
        no_api_modernization=no_api_modernization,
    )

    # 0. NO_EVOLUTION_REQUIRED — normal case, no exception detected.
    # Triggered when exception_type is "none" (or business_action is the
    # standard path). No proposal, no XAML change, no API deployment, no
    # trusted capability registration — the standard path needs no evolution.
    if exception_type.lower() in {"none", "no_exception", ""} or (
        business_action == "standard_purchase_order_processing"
    ):
        decision = {
            "case_id": case_id,
            "decision": "NO_EVOLUTION_REQUIRED",
            "business_action": business_action,
            "exception_type": exception_type,
            "api_modernization_required": False,
            "xaml_improvement_required": False,
            "requires_human_approval": False,
            "reason": (
                "Normal standard path requires no capability evolution."
            ),
            "next_stage": "STANDARD_PROCESSING",
            "status": "NO_EVOLUTION_REQUIRED",
        }
        decision.update(
            _build_evolution_explainability(
                decision_label="NO_EVOLUTION_REQUIRED", **explain_kwargs
            )
        )
        record_capability_evolution_decision(case_id, decision)
        return decision

    # 0b. MANUAL_INVESTIGATION — ambiguous / low-confidence case.
    # Triggered when exception_type is "unknown_exception" (triage could not
    # classify confidently) or business_action is the manual-review path.
    # No proposal, no XAML change, no API deployment, no trusted capability
    # registration — humans must investigate before any capability reuse.
    if exception_type.lower() in {"unknown_exception"} or (
        business_action == "manual_case_review"
    ):
        decision = {
            "case_id": case_id,
            "decision": "MANUAL_INVESTIGATION",
            "case_type": "ambiguous",
            "business_action": business_action,
            "exception_type": exception_type,
            "api_modernization_required": False,
            "xaml_improvement_required": False,
            "requires_human_review": True,
            "requires_human_approval": True,
            "reason": (
                "Low confidence or ambiguous business state requires human "
                "investigation before any capability evolution or reuse."
            ),
            "next_stage": "WAITING_MANUAL_INVESTIGATION",
            "status": "MANUAL_INVESTIGATION_REQUIRED",
        }
        decision.update(
            _build_evolution_explainability(
                decision_label="MANUAL_INVESTIGATION", **explain_kwargs
            )
        )
        record_capability_evolution_decision(case_id, decision)
        return decision

    # 1. USE_TRUSTED_CAPABILITY
    if trusted_capability_exists:
        decision = {
            "case_id": case_id,
            "decision": "USE_TRUSTED_CAPABILITY",
            "capability_id": capability.get("capability_id"),
            "execution_mode": capability.get("execution_mode"),
            "modernization_required": False,
            "reason": (
                "A trusted API capability is already registered for this "
                "business action."
            ),
        }
        decision.update(
            _build_evolution_explainability(
                decision_label="USE_TRUSTED_CAPABILITY", **explain_kwargs
            )
        )
        record_capability_evolution_decision(case_id, decision)
        return decision

    # 1b. Threshold-based proposal logic for REAL run-memory patterns.
    # When a real pattern file exists (source="real_run_memory"), use the
    # observed_count threshold instead of the legacy multi-condition logic.
    # This ensures proposals are generated from real accumulated evidence,
    # not from static demo seed data.
    real_source = real_pattern.get("source", "") if real_pattern else ""
    real_observed = int(real_pattern.get("observed_count", 0)) if real_pattern else 0
    _threshold = proposal_threshold()

    if real_source == "real_run_memory" and real_observed < _threshold:
        # KEEP_ACCUMULATING_EVIDENCE — below threshold, no proposal yet.
        decision = {
            "case_id": case_id,
            "decision": "KEEP_ACCUMULATING_EVIDENCE",
            "business_action": business_action,
            "exception_type": exception_type,
            "process_signature": process_signature,
            "observed_count": real_observed,
            "threshold": _threshold,
            "api_modernization_required": False,
            "xaml_improvement_required": False,
            "requires_human_approval": False,
            "requires_human_review": False,
            "reason": (
                f"Pattern has {real_observed} observation(s), below the "
                f"proposal threshold of {_threshold}. Continue accumulating "
                "evidence from real runs before generating a proposal."
            ),
            "next_stage": "ACCUMULATING_EVIDENCE",
            "status": "KEEP_ACCUMULATING_EVIDENCE",
        }
        decision.update(
            _build_evolution_explainability(
                decision_label="KEEP_ACCUMULATING_EVIDENCE", **explain_kwargs
            )
        )
        record_capability_evolution_decision(case_id, decision)
        return decision

    if real_source == "real_run_memory" and real_observed >= _threshold:
        # Threshold met — generate proposal based on exception type.
        if exception_type == "budget_exceeded" and not no_api_modernization:
            decision = {
                "case_id": case_id,
                "decision": "API_MODERNIZATION_PROPOSAL",
                "business_action": business_action,
                "exception_type": exception_type,
                "process_signature": process_signature,
                "observed_count": real_observed,
                "threshold": _threshold,
                "recommended_api": (
                    "POST /api/purchase-orders/{po_id}/approval-request"
                ),
                "reason": (
                    f"Real run memory pattern reached threshold "
                    f"({real_observed}>={_threshold}); propose API "
                    "modernization for budget approval."
                ),
                "requires_human_approval": True,
                "coding_agent_allowed": "after_approval_only",
                "status": "PROPOSAL_CREATED",
            }
            decision.update(
                _build_evolution_explainability(
                    decision_label="API_MODERNIZATION_PROPOSAL", **explain_kwargs
                )
            )
            record_capability_evolution_decision(case_id, decision)
            return decision

        if exception_type == "inventory_shortage":
            recommended_workflow = gap.get(
                "recommended_capability",
                f"Handle{_to_pascal_case(exception_type)}.xaml",
            )
            decision = {
                "case_id": case_id,
                "decision": "XAML_WORKFLOW_PROPOSAL",
                "business_action": business_action,
                "exception_type": exception_type,
                "process_signature": process_signature,
                "observed_count": real_observed,
                "threshold": _threshold,
                "recommended_change": f"Create {recommended_workflow} candidate",
                "reason": (
                    f"Real run memory pattern reached threshold "
                    f"({real_observed}>={_threshold}); propose XAML workflow "
                    "for inventory shortage capability gap."
                ),
                "requires_human_approval": True,
                "coding_agent_allowed": "after_approval_only",
                "status": "PROPOSAL_CREATED",
            }
            decision.update(
                _build_evolution_explainability(
                    decision_label="XAML_WORKFLOW_PROPOSAL", **explain_kwargs
                )
            )
            record_capability_evolution_decision(case_id, decision)
            return decision

        if no_api_modernization:
            decision = {
                "case_id": case_id,
                "decision": "WAIT_FOR_VENDOR_INFO",
                "next_stage": "WAITING_VENDOR_INFO",
                "api_modernization_required": False,
                "reason": (
                    "Vendor information is missing and the case is waiting for "
                    "business data completion."
                ),
                "status": "WAITING_BUSINESS_DATA",
            }
            decision.update(
                _build_evolution_explainability(
                    decision_label="WAIT_FOR_VENDOR_INFO", **explain_kwargs
                )
            )
            record_capability_evolution_decision(case_id, decision)
            return decision

        # For other exception types at threshold, fall through to legacy logic.

    # 2. API_MODERNIZATION_PROPOSAL (legacy multi-condition logic for seeded data)
    if (
        observed_count >= 5
        and business_value >= 0.75
        and field_stability >= 0.75
        and (validation_pass_rate >= 0.8 or validation_passed_now)
        and not no_api_modernization
    ):
        decision = {
            "case_id": case_id,
            "decision": "API_MODERNIZATION_PROPOSAL",
            "business_action": business_action,
            "recommended_api": (
                "POST /api/purchase-orders/{po_id}/approval-request"
            ),
            "reason": (
                "Repeated, high-value, stable business action with passed "
                "validation and parity evidence."
            ),
            "requires_human_approval": True,
            "coding_agent_allowed": "after_approval_only",
            "status": "PROPOSAL_CREATED",
        }
        decision.update(
            _build_evolution_explainability(
                decision_label="API_MODERNIZATION_PROPOSAL", **explain_kwargs
            )
        )
        record_capability_evolution_decision(case_id, decision)
        return decision

    # 3. XAML_IMPROVEMENT_PROPOSAL (selector fragility)
    if selector_failure_count >= 3:
        decision = {
            "case_id": case_id,
            "decision": "XAML_IMPROVEMENT_PROPOSAL",
            "business_action": business_action,
            "recommended_change": (
                "Improve selector strategy, retry logic, and exception "
                "handling in existing UiPath workflow."
            ),
            "reason": (
                f"Selector fragility detected ({selector_failure_count} "
                "failures); existing workflow needs hardening, not replacement."
            ),
            "requires_human_approval": True,
            "coding_agent_allowed": "after_approval_only",
            "status": "PROPOSAL_CREATED",
        }
        decision.update(
            _build_evolution_explainability(
                decision_label="XAML_IMPROVEMENT_PROPOSAL", **explain_kwargs
            )
        )
        record_capability_evolution_decision(case_id, decision)
        return decision

    # 3b. XAML_WORKFLOW_PROPOSAL (repeated gap, no trusted coverage)
    if repeated_gap_count >= 3 and not trusted_capability_found:
        recommended_workflow = gap.get(
            "recommended_capability",
            f"Handle{_to_pascal_case(exception_type)}.xaml",
        )
        decision = {
            "case_id": case_id,
            "decision": "XAML_WORKFLOW_PROPOSAL",
            "business_action": business_action,
            "recommended_change": f"Create {recommended_workflow} candidate",
            "reason": (
                f"Repeated {exception_type.replace('_', ' ')} cases have no "
                "trusted workflow or API coverage."
            ),
            "requires_human_approval": True,
            "coding_agent_allowed": "after_approval_only",
            "status": "PROPOSAL_CREATED",
        }
        decision.update(
            _build_evolution_explainability(
                decision_label="XAML_WORKFLOW_PROPOSAL", **explain_kwargs
            )
        )
        record_capability_evolution_decision(case_id, decision)
        return decision

    # 4a. WAIT_FOR_VENDOR_INFO — business data missing, not an automation problem
    if no_api_modernization:
        decision = {
            "case_id": case_id,
            "decision": "WAIT_FOR_VENDOR_INFO",
            "next_stage": "WAITING_VENDOR_INFO",
            "api_modernization_required": False,
            "reason": (
                "Vendor information is missing and the case is waiting for "
                "business data completion."
            ),
            "status": "WAITING_BUSINESS_DATA",
        }
        decision.update(
            _build_evolution_explainability(
                decision_label="WAIT_FOR_VENDOR_INFO", **explain_kwargs
            )
        )
        record_capability_evolution_decision(case_id, decision)
        return decision

    # 4b. KEEP_RPA_MODE / MANUAL_INVESTIGATION
    if field_stability and field_stability < 0.6:
        reason = (
            "The action is not stable enough for trusted API or workflow "
            "registration."
        )
    elif validation and not validation_passed_now:
        reason = "Validation has not passed; cannot trust the candidate yet."
    else:
        reason = (
            "Insufficient evidence to propose evolution; keep current RPA mode "
            "pending manual investigation."
        )
    decision = {
        "case_id": case_id,
        "decision": "KEEP_RPA_MODE",
        "reason": reason,
        "next_stage": "WAITING_MANUAL_INVESTIGATION",
        "requires_human_review": True,
    }
    decision.update(
        _build_evolution_explainability(
            decision_label="KEEP_RPA_MODE", **explain_kwargs
        )
    )
    record_capability_evolution_decision(case_id, decision)
    return decision


@app.post("/capability-evolution/decision")
def capability_evolution_decision(
    payload: CapabilityEvolutionEvaluateRequest,
) -> dict[str, Any]:
    """Evaluate capability-evolution route for a case and persist the decision.

    Note: this is the broader 4-branch decision evaluator (Task 3). The
    pre-existing ``/capability-evolution/evaluate`` endpoint implements the
    PRD 18.2 repeated-gap trigger and remains unchanged.
    """
    return evaluate_capability_evolution(payload)


# ---------------------------------------------------------------------------
# Case Dashboard (Task 4) — HTML for screencast demonstration
# ---------------------------------------------------------------------------

# Static demo data for CASE-002 and CASE-003, which don't have full memory/data
# JSON artifacts yet. CASE-001 reads its rich artifacts from memory/data.
_DASHBOARD_STATIC: dict[str, dict[str, Any]] = {
    "CASE-000": {
        "po_id": "PO-1000",
        "detected_exception_type": "none",
        "current_stage": "STANDARD_PROCESSING",
        "risk_level": "low",
        "execution_mode": "STANDARD",
        "trusted_tool_status": "not_applicable",
        "timeline": [
            "CASE_CREATED",
            "PRECHECK_COMPLETED",
            "STANDARD_PROCESSING_SELECTED",
            "RUN_COMPLETED",
        ],
        "decision_panel": {
            "selected_route": "STANDARD_PROCESSING",
            "agent_confidence": 0.97,
            "requires_human_approval": False,
            "capability_evolution_decision": "NO_EVOLUTION_REQUIRED",
            "reason": "Normal standard path requires no capability evolution.",
        },
        "validation_panel": None,
        "memory_panel": {
            "observed_count": 0,
            "repeated_gap_count": 0,
            "validation_pass_rate": None,
            "field_stability": None,
            "business_value": None,
            "ui_fragility": None,
            "selector_failure_count": None,
            "source": "no_pattern_required",
        },
        "registry_panel": {
            "trusted_capability_list": [],
            "registered_api_tools": [],
            "proposed_xaml_workflows": [],
            "capability_gaps": [],
        },
    },
    "CASE-002": {
        "po_id": "PO-1002",
        "detected_exception_type": "vendor_info_missing",
        "current_stage": "WAITING_VENDOR_INFO",
        "risk_level": "medium",
        "execution_mode": "RPA",
        "trusted_tool_status": "not_applicable",
        "timeline": [
            "CASE_CREATED",
            "RPA_EXTRACTED",
            "TRIAGE_COMPLETED",
            "ROUTED_TO_WAITING_VENDOR_INFO",
            "WAITING_VENDOR_INFO",
        ],
        "decision_panel": {
            "selected_route": "WAIT_FOR_VENDOR_INFO",
            "agent_confidence": 0.82,
            "requires_human_approval": False,
            "capability_evolution_decision": "WAIT_FOR_VENDOR_INFO",
            "reason": "Vendor information is missing and the case is waiting for business data completion.",
        },
        "validation_panel": None,
        "memory_panel": {
            "observed_count": 5,
            "repeated_gap_count": 0,
            "validation_pass_rate": None,
            "field_stability": None,
            "business_value": None,
            "ui_fragility": None,
            "selector_failure_count": None,
            "source": "demo_seeded_history",
        },
        "registry_panel": {
            "trusted_capability_list": [],
            "registered_api_tools": [],
            "proposed_xaml_workflows": [],
            "capability_gaps": [],
        },
    },
    "CASE-003": {
        "po_id": "PO-1003",
        "detected_exception_type": "inventory_shortage",
        "current_stage": "WAITING_HUMAN_REVIEW",
        "risk_level": "high",
        "execution_mode": "RPA",
        "trusted_tool_status": "no_trusted_capability",
        "timeline": [
            "CASE_CREATED",
            "TRIAGE_COMPLETED",
            "CAPABILITY_GAP_DETECTED",
            "XAML_WORKFLOW_PROPOSAL_CREATED",
            "WAITING_HUMAN_REVIEW",
        ],
        "decision_panel": {
            "selected_route": "XAML_WORKFLOW_PROPOSAL",
            "agent_confidence": 0.76,
            "requires_human_approval": True,
            "capability_evolution_decision": "XAML_WORKFLOW_PROPOSAL",
            "reason": "Repeated inventory shortage cases have no trusted workflow or API coverage.",
        },
        "validation_panel": None,
        "memory_panel": {
            "observed_count": 9,
            "repeated_gap_count": 9,
            "validation_pass_rate": None,
            "field_stability": 0.55,
            "business_value": None,
            "ui_fragility": 0.72,
            "selector_failure_count": None,
            "source": "demo_seeded_history",
        },
        "registry_panel": {
            "trusted_capability_list": [],
            "registered_api_tools": [],
            "proposed_xaml_workflows": ["HandleInventoryShortageReview.xaml"],
            "capability_gaps": [
                {
                    "exception_type": "inventory_shortage",
                    "required_business_action": "request_inventory_review",
                    "coverage_status": "not_covered",
                }
            ],
        },
    },
    "CASE-004": {
        "po_id": "PO-1004",
        "detected_exception_type": "unknown_exception",
        "current_stage": "WAITING_MANUAL_INVESTIGATION",
        "risk_level": "unknown",
        "execution_mode": "NONE",
        "trusted_tool_status": "not_applicable",
        "timeline": [
            "CASE_CREATED",
            "PRECHECK_COMPLETED",
            "AGENT_SEMANTIC_ROUTING",
            "MANUAL_INVESTIGATION_REQUIRED",
            "RUN_COMPLETED",
        ],
        "decision_panel": {
            "selected_route": "MANUAL_INVESTIGATION",
            "agent_confidence": 0.4,
            "requires_human_approval": True,
            "capability_evolution_decision": "MANUAL_INVESTIGATION",
            "reason": (
                "Low confidence or ambiguous business state requires human "
                "investigation before any capability evolution or reuse."
            ),
        },
        "validation_panel": None,
        "memory_panel": {
            "observed_count": 0,
            "repeated_gap_count": 0,
            "validation_pass_rate": None,
            "field_stability": None,
            "business_value": None,
            "ui_fragility": None,
            "selector_failure_count": None,
            "source": "no_pattern_required",
        },
        "registry_panel": {
            "trusted_capability_list": [],
            "registered_api_tools": [],
            "proposed_xaml_workflows": [],
            "capability_gaps": [],
        },
    },
}


def _build_case_001_dashboard() -> dict[str, Any]:
    """Assemble CASE-001 dashboard data from memory/data artifacts."""
    from memory.store import read_json

    state = read_json("case_state_CASE-001.json", {})
    timeline = state.get("timeline") or read_json("case_timeline_CASE-001.json", [])
    agent_decision = read_json("agent_decision_CASE-001.json", {})
    validation = read_json("validation_result_CASE-001.json", {})
    readiness = read_json("modernization_readiness_CASE-001.json", {})
    plan = read_json("modernization_plan_CASE-001.json", {})
    evolution = read_json("capability_evolution_decision_CASE-001.json", {})
    registry = read_json("capability_registry.json", {})
    patterns = read_json("historical_patterns.json", [])
    pattern = next(
        (p for p in patterns if p.get("business_action") == "request_purchase_order_approval"),
        {},
    )
    rpa_trace = read_json("rpa_trace_CASE-001.json", {})

    observed = pattern.get("observed_count", 0)
    validation_pass_count = pattern.get("validation_pass_count", 0)
    validation_pass_rate = (
        validation_pass_count / observed if observed else None
    )

    return {
        "po_id": "PO-1001",
        "detected_exception_type": agent_decision.get(
            "detected_exception_type", "budget_exceeded"
        ),
        "current_stage": state.get("current_stage", timeline[-1] if timeline else ""),
        "risk_level": "medium",
        "execution_mode": "API",
        "trusted_tool_status": "trusted_api_registered",
        "timeline": timeline,
        "decision_panel": {
            "selected_route": "API_MODERNIZATION",
            "agent_confidence": agent_decision.get("confidence", 0.94),
            "requires_human_approval": True,
            "capability_evolution_decision": evolution.get(
                "decision", "USE_TRUSTED_CAPABILITY"
            ),
            "reason": evolution.get(
                "reason",
                "A trusted API capability is already registered for this business action.",
            ),
        },
        "validation_panel": {
            "contract_test": validation.get("contract_test"),
            "business_rule_test": validation.get("business_rule_test"),
            "rpa_api_parity_check": validation.get("rpa_api_parity_check"),
            "same_initial_state": validation.get("same_initial_state"),
            "data_isolation": validation.get("data_isolation"),
            "readiness_score": readiness.get("readiness_score"),
            "plan_id": plan.get("plan_id"),
            "proposed_endpoint": plan.get("proposed_endpoint"),
        },
        "memory_panel": {
            "observed_count": observed,
            "repeated_gap_count": pattern.get("repeated_gap_count", 0),
            "validation_pass_rate": validation_pass_rate,
            "field_stability": pattern.get("field_stability"),
            "business_value": pattern.get("business_value"),
            "ui_fragility": pattern.get("ui_fragility"),
            "selector_failure_count": pattern.get("selector_failure_count"),
            "source": pattern.get("source", "demo_seeded_history"),
        },
        "registry_panel": {
            "trusted_capability_list": [registry] if registry else [],
            "registered_api_tools": [
                registry.get("capability_id")
            ] if registry and registry.get("type") == "API_TOOL" else [],
            "proposed_xaml_workflows": [],
            "capability_gaps": [],
        },
    }


def _render_dashboard_html(case_id: str, data: dict[str, Any]) -> str:
    """Render the Case Dashboard as a self-contained HTML page."""
    timeline = data.get("timeline", [])
    timeline_steps = "".join(
        f'<li class="step">{i+1}. {step}</li>' for i, step in enumerate(timeline)
    )
    dp = data.get("decision_panel") or {}
    vp = data.get("validation_panel") or {}
    mp = data.get("memory_panel") or {}
    rp = data.get("registry_panel") or {}

    def _fmt(value: Any) -> str:
        if value is None:
            return '<span class="muted">—</span>'
        if isinstance(value, bool):
            return "✅ Yes" if value else "❌ No"
        if isinstance(value, float):
            return f"{value:.2f}"
        return str(value)

    validation_rows = ""
    if vp:
        for label, key in [
            ("Contract Test", "contract_test"),
            ("Business Rule Test", "business_rule_test"),
            ("RPA/API Parity Check", "rpa_api_parity_check"),
            ("Same Initial State", "same_initial_state"),
            ("Data Isolation", "data_isolation"),
            ("Readiness Score", "readiness_score"),
            ("Plan ID", "plan_id"),
            ("Proposed Endpoint", "proposed_endpoint"),
        ]:
            validation_rows += (
                f"<tr><td>{label}</td><td>{_fmt(vp.get(key))}</td></tr>"
            )
    else:
        validation_rows = '<tr><td colspan="2" class="muted">No validation data for this case.</td></tr>'

    memory_rows = ""
    for label, key in [
        ("Observed Count", "observed_count"),
        ("Repeated Gap Count", "repeated_gap_count"),
        ("Validation Pass Rate", "validation_pass_rate"),
        ("Field Stability", "field_stability"),
        ("Business Value", "business_value"),
        ("UI Fragility", "ui_fragility"),
        ("Selector Failure Count", "selector_failure_count"),
        ("Source", "source"),
    ]:
        memory_rows += f"<tr><td>{label}</td><td>{_fmt(mp.get(key))}</td></tr>"

    trusted_list = rp.get("trusted_capability_list", [])
    api_tools = rp.get("registered_api_tools", [])
    proposed_xaml = rp.get("proposed_xaml_workflows", [])
    gaps = rp.get("capability_gaps", [])

    def _list_items(items: list[Any], formatter=lambda x: str(x)) -> str:
        if not items:
            return '<p class="muted">None</p>'
        return "<ul>" + "".join(f"<li>{formatter(i)}</li>" for i in items) + "</ul>"

    trusted_html = _list_items(
        trusted_list,
        lambda c: f"{c.get('capability_id', '?')} ({c.get('type', '?')}, {c.get('status', '?')})",
    )
    api_html = _list_items(api_tools)
    xaml_html = _list_items(proposed_xaml)
    gaps_html = _list_items(
        gaps,
        lambda g: f"{g.get('exception_type', '?')} → {g.get('required_business_action', '?')} ({g.get('coverage_status', '?')})",
    )

    nav_cases = "".join(
        f'<a class="nav-case{" active" if c == case_id else ""}" href="/case-dashboard/{c}">{c}</a>'
        for c in ("CASE-000", "CASE-001", "CASE-002", "CASE-003", "CASE-004")
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Case Dashboard — {case_id}</title>
<style>
  :root {{
    --bg: #0f172a; --panel: #1e293b; --panel-2: #273449;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
    --green: #4ade80; --amber: #fbbf24; --red: #f87171; --border: #334155;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); }}
  header {{ background: linear-gradient(90deg, #1e293b, #0f172a); padding: 18px 32px; border-bottom: 1px solid var(--border); }}
  header h1 {{ margin: 0; font-size: 22px; }}
  header .subtitle {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  .nav {{ margin-top: 12px; display: flex; gap: 8px; }}
  .nav-case {{ padding: 6px 14px; border-radius: 6px; background: var(--panel-2); color: var(--muted); text-decoration: none; font-size: 13px; border: 1px solid var(--border); }}
  .nav-case.active {{ background: var(--accent); color: #0f172a; font-weight: 600; border-color: var(--accent); }}
  main {{ padding: 24px 32px; display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; }}
  .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 18px; }}
  .panel h2 {{ margin: 0 0 12px 0; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--accent); }}
  .panel.full {{ grid-column: 1 / -1; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  td {{ padding: 7px 8px; border-bottom: 1px solid var(--border); }}
  td:first-child {{ color: var(--muted); width: 45%; }}
  td:last-child {{ font-weight: 500; }}
  .timeline {{ list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 6px; }}
  .timeline .step {{ background: var(--panel-2); padding: 6px 12px; border-radius: 6px; font-size: 12px; border: 1px solid var(--border); }}
  .timeline .step:nth-last-child(1) {{ background: var(--green); color: #052e16; font-weight: 700; border-color: var(--green); }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .badge-api {{ background: rgba(56,189,248,0.18); color: var(--accent); }}
  .badge-rpa {{ background: rgba(251,191,36,0.18); color: var(--amber); }}
  .muted {{ color: var(--muted); }}
  ul {{ margin: 6px 0; padding-left: 20px; font-size: 13px; }}
  footer {{ padding: 16px 32px; color: var(--muted); font-size: 11px; border-top: 1px solid var(--border); }}
</style>
</head>
<body>
<header>
  <h1>Agentic ERP Modernization — Case Dashboard</h1>
  <div class="subtitle">UiPath-governed capability evolution demo · {case_id}</div>
  <div class="nav">{nav_cases}</div>
</header>
<main>
  <section class="panel">
    <h2>Case Overview</h2>
    <table>
      <tr><td>Case ID</td><td>{case_id}</td></tr>
      <tr><td>PO ID</td><td>{data.get('po_id', '—')}</td></tr>
      <tr><td>Detected Exception</td><td>{data.get('detected_exception_type', '—')}</td></tr>
      <tr><td>Current Stage</td><td>{data.get('current_stage', '—')}</td></tr>
      <tr><td>Risk Level</td><td>{data.get('risk_level', '—')}</td></tr>
      <tr><td>Execution Mode</td><td><span class="badge {'badge-api' if data.get('execution_mode')=='API' else 'badge-rpa'}">{data.get('execution_mode', '—')}</span></td></tr>
      <tr><td>Trusted Tool Status</td><td>{data.get('trusted_tool_status', '—')}</td></tr>
    </table>
  </section>

  <section class="panel">
    <h2>Decision Panel</h2>
    <table>
      <tr><td>Selected Route</td><td>{dp.get('selected_route', '—')}</td></tr>
      <tr><td>Agent Confidence</td><td>{_fmt(dp.get('agent_confidence'))}</td></tr>
      <tr><td>Requires Human Approval</td><td>{_fmt(dp.get('requires_human_approval'))}</td></tr>
      <tr><td>Capability Evolution Decision</td><td>{dp.get('capability_evolution_decision', '—')}</td></tr>
      <tr><td>Reason</td><td>{dp.get('reason', '—')}</td></tr>
    </table>
  </section>

  <section class="panel full">
    <h2>Timeline</h2>
    <ul class="timeline">{timeline_steps}</ul>
  </section>

  <section class="panel">
    <h2>Validation / Readiness Panel</h2>
    <table>{validation_rows}</table>
  </section>

  <section class="panel">
    <h2>Memory Evidence Panel</h2>
    <table>{memory_rows}</table>
  </section>

  <section class="panel full">
    <h2>Capability Registry / Gap Panel</h2>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;">
      <div><h3 style="font-size:12px;color:var(--muted);margin:0 0 6px;">Trusted Capabilities</h3>{trusted_html}</div>
      <div><h3 style="font-size:12px;color:var(--muted);margin:0 0 6px;">Registered API Tools</h3>{api_html}</div>
      <div><h3 style="font-size:12px;color:var(--muted);margin:0 0 6px;">Proposed XAML Workflows</h3>{xaml_html}</div>
      <div><h3 style="font-size:12px;color:var(--muted);margin:0 0 6px;">Capability Gaps</h3>{gaps_html}</div>
    </div>
  </section>
</main>
<footer>Source: demo_seeded_history where applicable · No real production data · Linux backend demo</footer>
</body>
</html>"""


def _resolve_run_id(case_id: str, run_id: str | None) -> str | None:
    """Resolve the run_id to display: explicit query param, or latest run."""
    if run_id:
        return run_id
    try:
        from memory.run_memory import case_dir
        latest_path = case_dir(case_id) / "latest_run_id.txt"
        if latest_path.exists():
            rid = latest_path.read_text(encoding="utf-8").strip()
            return rid or None
    except Exception:
        pass
    return None


def _build_run_memory_dashboard(case_id: str, run_id: str) -> dict[str, Any]:
    """Assemble dashboard data from real Run Memory artifacts."""
    from memory.run_memory import (
        run_dir,
        run_raw_dir,
        run_normalized_dir,
        run_summary_dir,
        run_evolution_dir,
        _read_json,
        _read_jsonl,
        list_raw_artifacts,
        ARTIFACT_TYPES,
    )

    rdir = run_dir(run_id)
    raw_dir = run_raw_dir(run_id)
    norm_dir = run_normalized_dir(run_id)
    summ_dir = run_summary_dir(run_id)
    evo_dir = run_evolution_dir(run_id)

    # Normalized case state.
    case_state = _read_json(norm_dir / "case_state.json", {}) or {}
    case_timeline = _read_json(norm_dir / "case_timeline.json", []) or []
    business_action = _read_json(norm_dir / "business_action.json", {}) or {}
    process_sig = _read_json(norm_dir / "process_signature.json", {}) or {}

    # Raw events.
    events = _read_jsonl(raw_dir / "uipath_execution_events.jsonl")

    # Raw artifacts (individual files).
    rpa_extracted = _read_json(raw_dir / "rpa_extracted_fields.json", {}) or {}
    rpa_click_trace = _read_json(raw_dir / "rpa_click_trace.json", {}) or {}
    rpa_selector_trace = _read_json(raw_dir / "rpa_selector_trace.json", {}) or {}
    agent_io = _read_json(raw_dir / "agent_input_output.json", {}) or {}
    company_context_snapshot = _read_json(raw_dir / "company_context_snapshot.json", {}) or {}
    route_plan = _read_json(raw_dir / "route_plan.json", {}) or {}
    agent_reasoning_summary = _read_json(raw_dir / "agent_reasoning_summary.json", {}) or {}
    llm_validation_proof = _read_json(raw_dir / "llm_validation_proof.json", {}) or {}
    policy_gate_artifact = _read_json(raw_dir / "policy_gate.json", {}) or {}
    selected_erp_action = _read_json(raw_dir / "selected_erp_action.json", {}) or {}
    final_branch_result = _read_json(raw_dir / "final_branch_result.json", {}) or {}
    human_approval = _read_json(raw_dir / "human_approval.json", {}) or {}
    validation_resp = _read_json(raw_dir / "validation_response.json", {}) or {}
    generated_api = _read_json(raw_dir / "generated_api_response.json", {}) or {}
    http_calls = _read_jsonl(raw_dir / "http_calls.jsonl")
    errors = _read_jsonl(raw_dir / "errors.jsonl")

    # Summary.
    case_run_summary = _read_json(summ_dir / "case_run_summary.json", {}) or {}
    post_run_summary = _read_json(summ_dir / "post_run_memory_summary.json", {}) or {}

    # Evolution.
    evolution_decision = _read_json(evo_dir / "capability_evolution_decision.json", {}) or {}
    pattern_update = _read_json(evo_dir / "pattern_update.json", {}) or {}

    # Proposal (if referenced by the evolution decision).
    proposal = None
    proposal_id = evolution_decision.get("proposal_id")
    if proposal_id:
        from memory.run_memory import proposal_path
        proposal = _read_json(proposal_path(proposal_id), None)

    # List of memory files written.
    memory_files: dict[str, list[str]] = {"raw": [], "normalized": [], "summary": [], "evolution": []}
    for label, directory in [
        ("raw", raw_dir),
        ("normalized", norm_dir),
        ("summary", summ_dir),
        ("evolution", evo_dir),
    ]:
        if directory.exists():
            for child in sorted(directory.iterdir()):
                if child.is_file():
                    memory_files[label].append(child.name)

    # Pattern file.
    pattern_file_path = None
    pattern_data = None
    sig = process_sig.get("process_signature") or ""
    if sig:
        from memory.patterns import load_pattern
        pattern_data = load_pattern(sig)
        if pattern_data:
            pattern_file_path = f"memory/patterns/{sig}.json"

    return {
        "case_id": case_id,
        "run_id": run_id,
        "po_id": case_state.get("po_id"),
        "current_stage": case_state.get("current_stage") or case_state.get("final_stage"),
        "execution_mode": case_state.get("execution_mode"),
        "result": case_state.get("result"),
        "trusted_tool_status": evolution_decision.get("decision", "—"),
        "business_action": business_action.get("business_action"),
        "process_signature": sig,
        "events": events,
        "case_timeline": case_timeline,
        "rpa_extracted_fields": rpa_extracted.get("data", rpa_extracted),
        "rpa_click_trace": rpa_click_trace.get("data", rpa_click_trace),
        "rpa_selector_trace": rpa_selector_trace.get("data", rpa_selector_trace),
        "agent_io": agent_io.get("data", agent_io),
        "company_context_snapshot": company_context_snapshot.get("data", company_context_snapshot),
        "route_plan": route_plan.get("data", route_plan),
        "agent_reasoning_summary": agent_reasoning_summary.get("data", agent_reasoning_summary),
        "llm_validation_proof": llm_validation_proof.get("data", llm_validation_proof),
        "policy_gate_artifact": policy_gate_artifact.get("data", policy_gate_artifact),
        "selected_erp_action": selected_erp_action.get("data", selected_erp_action),
        "final_branch_result": final_branch_result.get("data", final_branch_result),
        "human_approval": human_approval.get("data", human_approval),
        "validation_response": validation_resp.get("data", validation_resp),
        "generated_api_response": generated_api.get("data", generated_api),
        "http_calls": http_calls,
        "errors": errors,
        "case_run_summary": case_run_summary,
        "post_run_summary": post_run_summary,
        "evolution_decision": evolution_decision,
        "pattern_update": pattern_update,
        "proposal": proposal,
        "memory_files": memory_files,
        "pattern_file_path": pattern_file_path,
        "pattern_data": pattern_data,
    }


def _render_run_memory_html(case_id: str, data: dict[str, Any]) -> str:
    """Render the real-run-memory dashboard as a self-contained HTML page."""
    import html as html_lib

    def _esc(val: Any) -> str:
        return html_lib.escape(str(val)) if val is not None else ""

    def _fmt(value: Any) -> str:
        if value is None:
            return '<span class="muted">—</span>'
        if isinstance(value, bool):
            return "✅ Yes" if value else "❌ No"
        if isinstance(value, float):
            return f"{value:.4f}"
        return _esc(value)

    def _json_block(obj: Any, title: str = "JSON") -> str:
        if not obj:
            return '<span class="muted">Not captured</span>'
        formatted = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
        return f'<details class="json-details"><summary>{_esc(title)}</summary><pre class="json">{_esc(formatted)}</pre></details>'

    def _artifact_table(items: list[tuple[str, Any, str]]) -> str:
        captured = sum(1 for _, obj, _ in items if obj)
        rows = ""
        for label, obj, title in items:
            if obj:
                status_html = '<span class="pill pill-ok">Captured</span>'
                detail_html = _json_block(obj, title)
            else:
                status_html = '<span class="pill">Empty</span>'
                detail_html = '<span class="muted">Not captured for this run.</span>'
            rows += (
                f"<tr><td>{_esc(label)}</td>"
                f"<td>{status_html}</td>"
                f"<td>{detail_html}</td></tr>"
            )
        return (
            f"<p class='audit-note'>{captured} of {len(items)} artifacts captured.</p>"
            "<table class='artifact-table'><thead><tr><th>Artifact</th>"
            "<th>Status</th><th>Evidence</th></tr></thead><tbody>"
            f"{rows}</tbody></table>"
        )

    run_id = data.get("run_id", "—")
    events = data.get("events", [])
    timeline = data.get("case_timeline", [])

    # Timeline rows.
    event_rows = "".join(
        f"<tr><td>{_esc(e.get('occurred_at', '—'))}</td>"
        f"<td><strong>{_esc(e.get('event_type', '—'))}</strong></td>"
        f"<td>{_esc(e.get('stage', '—'))}</td>"
        f"<td>{_esc(e.get('status', '—'))}</td></tr>"
        for e in events
    ) or '<tr><td colspan="4" class="muted">No events recorded.</td></tr>'

    # HTTP calls.
    http_rows = "".join(
        f"<tr><td>{_esc(c.get('endpoint', '—'))}</td>"
        f"<td>{_esc(c.get('method', '—'))}</td>"
        f"<td>{_esc(c.get('status_code', '—'))}</td></tr>"
        for c in data.get("http_calls", [])
    ) or '<tr><td colspan="3" class="muted">No HTTP calls recorded.</td></tr>'

    # Memory files written.
    mem_files = data.get("memory_files", {})
    mem_files_html = ""
    for category in ("raw", "normalized", "summary", "evolution"):
        files = mem_files.get(category, [])
        items = "".join(f"<li>{_esc(f)}</li>" for f in files) or '<li class="muted">None</li>'
        mem_files_html += f"<div><h4>{category}</h4><ul>{items}</ul></div>"

    # Pattern update before/after.
    pu = data.get("pattern_update", {}) or {}
    pu_before = pu.get("before", {})
    pu_after = pu.get("after", {})
    pu_changed = pu.get("changed_fields", [])
    pu_rows = ""
    if pu_changed:
        for cf in pu_changed:
            pu_rows += (
                f"<tr><td>{_esc(cf.get('field', '—'))}</td>"
                f"<td>{_fmt(cf.get('before'))}</td>"
                f"<td>{_fmt(cf.get('after'))}</td></tr>"
            )
    else:
        pu_rows = '<tr><td colspan="3" class="muted">No pattern update for this run.</td></tr>'

    # Evolution decision.
    ed = data.get("evolution_decision", {}) or {}
    rule_eval = ed.get("rule_evaluation", {}) or {}
    rule_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td>{'✅ true' if v else '❌ false'}</td></tr>"
        for k, v in rule_eval.items()
    ) or '<tr><td colspan="2" class="muted">No rule evaluation.</td></tr>'

    why_not = ed.get("why_not", {}) or {}
    why_not_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>"
        for k, v in why_not.items()
    ) or '<tr><td colspan="2" class="muted">No why_not data.</td></tr>'

    evidence = ed.get("evidence", {}) or {}
    evidence_run_ids = evidence.get("evidence_run_ids", [])
    artifact_capture_count = sum(
        1
        for obj in [
            data.get("rpa_extracted_fields"),
            data.get("rpa_click_trace"),
            data.get("rpa_selector_trace"),
            data.get("agent_io"),
            data.get("validation_response"),
            data.get("generated_api_response"),
        ]
        if obj
    )
    memory_file_count = sum(len(files) for files in (data.get("memory_files", {}) or {}).values())
    rpa_fields = data.get("rpa_extracted_fields") or {}
    route_plan = data.get("route_plan") or {}
    company_context_used = data.get("company_context_snapshot") or route_plan.get("company_context_snapshot") or {}
    agent_decision = {
        "final_route": route_plan.get("final_route"),
        "agent_context_used": route_plan.get("agent_context_used"),
        "company_context_reference": route_plan.get("company_context_reference"),
        "agent_reasoning_summary": (
            route_plan.get("agent_reasoning_summary")
            or (data.get("agent_reasoning_summary") or {}).get("agent_reasoning_summary")
        ),
        "llm_validation_proof": route_plan.get("llm_validation_proof") or data.get("llm_validation_proof"),
    }
    context_reference = route_plan.get("company_context_reference") or {}
    if not context_reference and company_context_used:
        context_reference = {
            "finance_policy_used": bool((company_context_used.get("company") or {}).get("finance_policy")),
            "sales_context_used": bool((company_context_used.get("company") or {}).get("sales_context")),
            "operations_context_used": bool((company_context_used.get("company") or {}).get("operations_context")),
        }
    llm_proof_view = agent_decision.get("llm_validation_proof") or {}
    policy_gate_view = (
        data.get("policy_gate_artifact")
        or route_plan.get("policy_gate")
        or ed.get("policy_gate")
        or {}
    )
    selected_action = (
        data.get("selected_erp_action")
        or route_plan.get("recommended_erp_action")
        or {}
    )
    final_branch = data.get("final_branch_result") or {
        "result": data.get("result"),
        "current_stage": data.get("current_stage"),
        "execution_mode": data.get("execution_mode"),
    }

    # Proposal.
    proposal = data.get("proposal")
    proposal_html = ""
    if proposal:
        lifecycle = proposal.get("lifecycle", [])
        lifecycle_html = "".join(
            f"<li>{_esc(s.get('stage', '—'))} <span class='muted'>({_esc(s.get('actor', '—'))}, {_esc(s.get('timestamp', '—'))})</span></li>"
            for s in lifecycle
        )
        proposal_html = f"""
        <table>
          <tr><td>Proposal ID</td><td>{_esc(proposal.get('proposal_id', '—'))}</td></tr>
          <tr><td>Proposal Type</td><td>{_esc(proposal.get('proposal_type', '—'))}</td></tr>
          <tr><td>Status</td><td><strong>{_esc(proposal.get('status', '—'))}</strong></td></tr>
          <tr><td>Recommended Change</td><td>{_esc(proposal.get('recommended_change', '—'))}</td></tr>
          <tr><td>Requires Human Approval</td><td>{_fmt(proposal.get('requires_human_approval'))}</td></tr>
          <tr><td>Coding Agent Allowed</td><td>{_esc(proposal.get('coding_agent_allowed', '—'))}</td></tr>
          <tr><td>Auto Execution Allowed</td><td>{_fmt(proposal.get('auto_execution_allowed'))}</td></tr>
        </table>
        <h4>Lifecycle</h4>
        <ul class="lifecycle">{lifecycle_html}</ul>
        """
    else:
        proposal_html = '<p class="muted">No proposal generated for this run.</p>'

    # Trusted capability registry.
    from memory.store import read_json
    registry = read_json("capability_registry.json", {})

    nav_cases = "".join(
        f'<a class="nav-case{" active" if c == case_id else ""}" href="/case-dashboard/{c}">{c}</a>'
        for c in ("CASE-000", "CASE-001", "CASE-002", "CASE-003", "CASE-004")
    )
    process_signature = data.get("process_signature") or ""
    pattern_link = (
        f'<a class="nav-case" href="/patterns/{urllib.parse.quote(process_signature, safe="")}">Pattern Detail</a>'
        if process_signature else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Run Memory Dashboard — {case_id} / {run_id}</title>
<style>
  :root {{
    --bg: #0f172a; --panel: #1e293b; --panel-2: #273449;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
    --green: #4ade80; --amber: #fbbf24; --red: #f87171; --border: #334155;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); font-size: 13px; }}
  header {{ background: linear-gradient(90deg, #1e293b, #0f172a); padding: 14px 24px; border-bottom: 1px solid var(--border); }}
  header h1 {{ margin: 0; font-size: 20px; }}
  header .subtitle {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  .nav {{ margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }}
  .nav-label {{ color: var(--muted); font-size: 11px; align-self: center; margin-right: 2px; }}
  .nav-case {{ padding: 5px 10px; border-radius: 4px; background: var(--panel-2); color: var(--muted); text-decoration: none; font-size: 12px; border: 1px solid var(--border); }}
  .nav-case.active {{ background: var(--accent); color: #0f172a; font-weight: 600; border-color: var(--accent); }}
  main {{ padding: 14px 18px 18px; display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 10px; align-items: start; }}
  .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 12px; align-self: start; }}
  .panel h2 {{ margin: 0 0 8px 0; font-size: 13px; text-transform: uppercase; letter-spacing: 0.3px; color: var(--accent); }}
  .panel h4 {{ margin: 8px 0 6px; font-size: 12px; color: var(--muted); }}
  .panel.full {{ grid-column: 1 / -1; }}
  .span-4 {{ grid-column: span 4; }}
  .span-5 {{ grid-column: span 5; }}
  .span-6 {{ grid-column: span 6; }}
  .span-7 {{ grid-column: span 7; }}
  .span-8 {{ grid-column: span 8; }}
  .kpi-strip {{ grid-column: 1 / -1; display: grid; grid-template-columns: repeat(6, minmax(110px, 1fr)); gap: 8px; }}
  .kpi {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 9px 10px; min-height: 58px; }}
  .kpi b {{ display: block; color: var(--text); font-size: 13px; overflow-wrap: anywhere; }}
  .kpi span {{ display: block; color: var(--muted); font-size: 11px; margin-top: 3px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td, th {{ padding: 5px 7px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  th {{ color: var(--muted); text-align: left; font-size: 11px; font-weight: 600; }}
  td:first-child {{ color: var(--muted); width: 40%; }}
  td:last-child {{ font-weight: 500; word-break: break-all; }}
  .muted {{ color: var(--muted); }}
  .audit-note {{ color: var(--muted); margin: 0 0 8px; font-size: 12px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .badge-api {{ background: rgba(56,189,248,0.18); color: var(--accent); }}
  .badge-rpa {{ background: rgba(251,191,36,0.18); color: var(--amber); }}
  .pill {{ display: inline-block; padding: 2px 7px; border: 1px solid var(--border); border-radius: 999px; color: var(--muted); font-size: 11px; white-space: nowrap; }}
  .pill-ok {{ border-color: rgba(74,222,128,0.45); color: var(--green); background: rgba(74,222,128,0.09); }}
  pre.json {{ background: var(--panel-2); border: 1px solid var(--border); border-radius: 6px; padding: 8px; overflow-x: auto; font-size: 11px; line-height: 1.35; white-space: pre-wrap; word-break: break-all; max-height: 300px; }}
  details {{ margin-top: 4px; }}
  .json-details {{ margin: 0; }}
  summary {{ cursor: pointer; color: var(--accent); font-size: 12px; }}
  ul {{ margin: 4px 0; padding-left: 18px; }}
  ul.lifecycle li {{ margin-bottom: 4px; }}
  .mem-files {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }}
  .mem-files h4 {{ font-size: 12px; color: var(--muted); margin: 0 0 6px; }}
  .mem-files ul {{ font-size: 11px; padding-left: 15px; }}
  .artifact-table td:first-child {{ width: 28%; }}
  .artifact-table td:nth-child(2) {{ width: 72px; }}
  footer {{ padding: 12px 24px; color: var(--muted); font-size: 11px; border-top: 1px solid var(--border); }}
  @media (max-width: 1100px) {{
    main {{ grid-template-columns: 1fr; }}
    .span-4, .span-5, .span-6, .span-7, .span-8, .panel.full {{ grid-column: 1 / -1; }}
    .kpi-strip {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
    .mem-files {{ grid-template-columns: repeat(2, 1fr); }}
  }}
</style>
</head>
<body>
<header>
  <h1>Run Memory Dashboard — Pattern Evidence Detail</h1>
  <div class="subtitle">Case {case_id} · Run {run_id} · raw events, artifacts, process signature, and capability decision</div>
  <div class="nav">
    <a class="nav-case" href="/simulation/dashboard">Pattern Memory Dashboard</a>
    <a class="nav-case" href="/agent-trace/{_esc(run_id)}">Agent Trace</a>
    {pattern_link}
    <a class="nav-case" href="/proposals/inbox">Proposal Pipeline</a>
    <span class="nav-label">Demo samples:</span>{nav_cases}
  </div>
</header>
<main>
  <section class="kpi-strip">
    <div class="kpi"><b>{_esc(data.get('result', '—'))}</b><span>Final Result</span></div>
    <div class="kpi"><b>{_esc(data.get('execution_mode', '—'))}</b><span>Execution Mode</span></div>
    <div class="kpi"><b>{len(events)}</b><span>Events</span></div>
    <div class="kpi"><b>{artifact_capture_count}/6</b><span>Artifacts Captured</span></div>
    <div class="kpi"><b>{memory_file_count}</b><span>Memory Files</span></div>
    <div class="kpi"><b>{_esc(ed.get('decision', '—'))}</b><span>Evolution Decision</span></div>
  </section>

  <section class="panel span-5">
    <h2>Case Overview</h2>
    <table>
      <tr><td>Case ID</td><td>{_esc(case_id)}</td></tr>
      <tr><td>Run ID</td><td><strong>{_esc(run_id)}</strong></td></tr>
      <tr><td>PO ID</td><td>{_esc(data.get('po_id', '—'))}</td></tr>
      <tr><td>Business Action</td><td>{_esc(data.get('business_action', '—'))}</td></tr>
      <tr><td>Process Signature</td><td>{_esc(data.get('process_signature', '—'))}</td></tr>
      <tr><td>Current Stage</td><td>{_esc(data.get('current_stage', '—'))}</td></tr>
      <tr><td>Execution Mode</td><td><span class="badge {'badge-api' if data.get('execution_mode')=='API' else 'badge-rpa'}">{_esc(data.get('execution_mode', '—'))}</span></td></tr>
      <tr><td>Final Result</td><td>{_esc(data.get('result', '—'))}</td></tr>
      <tr><td>Trusted Tool Status</td><td>{_esc(data.get('trusted_tool_status', '—'))}</td></tr>
    </table>
  </section>

  <section class="panel span-7">
    <h2>Capability Evolution Decision</h2>
    <table>
      <tr><td>Decision</td><td><strong>{_esc(ed.get('decision', '—'))}</strong></td></tr>
      <tr><td>Reason</td><td>{_esc(ed.get('reason', '—'))}</td></tr>
      <tr><td>Recommended API</td><td>{_esc(ed.get('recommended_api', '—'))}</td></tr>
      <tr><td>Recommended Change</td><td>{_esc(ed.get('recommended_change', '—'))}</td></tr>
      <tr><td>Requires Human Approval</td><td>{_fmt(ed.get('requires_human_approval'))}</td></tr>
      <tr><td>Coding Agent Allowed</td><td>{_esc(ed.get('coding_agent_allowed', '—'))}</td></tr>
      <tr><td>Proposal ID</td><td>{_esc(ed.get('proposal_id', '—'))}</td></tr>
      <tr><td>Evidence Run IDs</td><td>{_esc(', '.join(evidence_run_ids) if evidence_run_ids else '—')}</td></tr>
    </table>
    <details><summary>Rule Evaluation</summary><table>{rule_rows}</table></details>
    <details><summary>Why Not (Alternative Decisions)</summary><table>{why_not_rows}</table></details>
  </section>

  <section class="panel full">
    <h2>ERP Order Fields</h2>
    <table>
      <tr><td>PO Number</td><td>{_esc(rpa_fields.get('po_id') or data.get('po_id') or '—')}</td></tr>
      <tr><td>Amount</td><td>{_esc(rpa_fields.get('amount', '—'))}</td></tr>
      <tr><td>Budget Limit</td><td>{_esc(rpa_fields.get('budget_limit', '—'))}</td></tr>
      <tr><td>Vendor ID</td><td>{_esc(rpa_fields.get('vendor_id', '—'))}</td></tr>
      <tr><td>ERP Status</td><td>{_esc(rpa_fields.get('erp_status', '—'))}</td></tr>
      <tr><td>System Message</td><td>{_esc(rpa_fields.get('raw_exception_text', '—'))}</td></tr>
      <tr><td>Business Remarks</td><td>{_esc(rpa_fields.get('business_remarks') or route_plan.get('business_remarks') or '—')}</td></tr>
    </table>
  </section>

  <section class="panel span-6">
    <h2>Company Context Used</h2>
    <table>
      <tr><td>Context Source</td><td><strong>/company-context</strong></td></tr>
      <tr><td>Agent Context Used</td><td>{_fmt(bool(route_plan.get('agent_context_used') or context_reference))}</td></tr>
      <tr><td>Finance Policy Used</td><td>{_fmt(context_reference.get('finance_policy_used'))}</td></tr>
      <tr><td>Sales Context Used</td><td>{_fmt(context_reference.get('sales_context_used'))}</td></tr>
      <tr><td>Operations Context Used</td><td>{_fmt(context_reference.get('operations_context_used'))}</td></tr>
    </table>
    {_json_block(company_context_used, 'company_context_snapshot.json')}
  </section>

  <section class="panel span-6">
    <h2>Agent Decision</h2>
    <table>
      <tr><td>Final Route</td><td>{_esc(agent_decision.get('final_route') or '—')}</td></tr>
      <tr><td>Agent Reasoning Summary</td><td>{_esc(agent_decision.get('agent_reasoning_summary') or '—')}</td></tr>
      <tr><td>Reasoning Mode</td><td>{_esc(llm_proof_view.get('reasoning_mode') or '—')}</td></tr>
      <tr><td>LLM Provider</td><td>{_esc(llm_proof_view.get('llm_provider') or '—')}</td></tr>
      <tr><td>LLM Call Mode</td><td>{_esc(llm_proof_view.get('llm_call_mode') or '—')}</td></tr>
      <tr><td>LLM Invocation Verified</td><td>{_fmt(llm_proof_view.get('llm_invocation_verified'))}</td></tr>
      <tr><td>Guardrails Applied</td><td>{_fmt(llm_proof_view.get('guardrails_applied'))}</td></tr>
    </table>
    {_json_block(agent_decision, 'agent decision proof')}
  </section>

  <section class="panel span-4">
    <h2>Policy Gate</h2>
    {_json_block(policy_gate_view, 'policy_gate.json')}
  </section>

  <section class="panel span-4">
    <h2>UiPath RPA Action</h2>
    {_json_block(selected_action, 'selected_erp_action.json')}
  </section>

  <section class="panel span-4">
    <h2>Memory Commit</h2>
    {_json_block(final_branch, 'final_branch_result.json')}
  </section>

  <section class="panel full">
    <h2>Live Timeline / Event Log</h2>
    <table>
      <thead><tr><th>Timestamp</th><th>Event Type</th><th>Stage</th><th>Status</th></tr></thead>
      <tbody>{event_rows}</tbody>
    </table>
  </section>

  <section class="panel span-6">
    <h2>Raw UiPath / RPA Trace</h2>
    {_artifact_table([
        ('RPA Extracted Fields', data.get('rpa_extracted_fields'), 'rpa_extracted_fields.json'),
        ('RPA Click Trace', data.get('rpa_click_trace'), 'rpa_click_trace.json'),
        ('RPA Selector Trace', data.get('rpa_selector_trace'), 'rpa_selector_trace.json'),
    ])}
  </section>

  <section class="panel span-6">
    <h2>Agent I/O</h2>
    {_artifact_table([
        ('Agent Input / Output', data.get('agent_io'), 'agent_input_output.json'),
        ('Validation Response', data.get('validation_response'), 'validation_response.json'),
        ('Generated API Response', data.get('generated_api_response'), 'generated_api_response.json'),
    ])}
  </section>

  <section class="panel span-4">
    <h2>HTTP Call Trace</h2>
    <table>
      <thead><tr><th>Endpoint</th><th>Method</th><th>Status Code</th></tr></thead>
      <tbody>{http_rows}</tbody>
    </table>
    {_json_block(data.get('http_calls'), 'http_calls.jsonl (full)')}
  </section>

  <section class="panel span-8">
    <h2>Memory Written</h2>
    <div class="mem-files">{mem_files_html}</div>
    <h4>Pattern File</h4>
    <p>{_esc(data.get('pattern_file_path') or 'No pattern file updated.')}</p>
    {_json_block(data.get('pattern_data'), 'pattern file content')}
  </section>

  <section class="panel full">
    <h2>Historical Pattern Update</h2>
    <table>
      <thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead>
      <tbody>{pu_rows}</tbody>
    </table>
    <details><summary>Full before / after pattern snapshots</summary>
      {_json_block(pu_before, 'pattern before')}
      {_json_block(pu_after, 'pattern after')}
    </details>
  </section>

  <section class="panel full">
    <h2>Proposal / Registry Lifecycle</h2>
    {proposal_html}
    <h4 style="margin-top:16px;">Trusted Capability Registry</h4>
    {_json_block(registry if registry else None, 'capability_registry.json')}
  </section>

  <section class="panel full">
    <h2>Case Run Summary</h2>
    {_json_block(data.get('case_run_summary'), 'case_run_summary.json')}
    <h4 style="margin-top:12px;">Post-Run Memory Summary</h4>
    {_json_block(data.get('post_run_summary'), 'post_run_memory_summary.json')}
  </section>
</main>
<footer>Source: Real Run Memory (memory/runs/) · Structured Memory is the system of record</footer>
</body>
</html>"""


def _artifact_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _decode_base64_json_response(artifact_data: dict[str, Any]) -> tuple[Any, str, str | None]:
    encoded = artifact_data.get("response_json_base64")
    if not encoded:
        return None, "", None
    try:
        import base64

        decoded = base64.b64decode(str(encoded)).decode("utf-8")
    except Exception as exc:
        return None, "", f"base64 decode failed: {exc}"
    if not decoded.strip():
        return None, decoded, None
    try:
        return json.loads(decoded), decoded, None
    except json.JSONDecodeError as exc:
        return {"raw_response": decoded}, decoded, f"json parse failed: {exc}"


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return {}


def _first_present(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


def _build_agent_trace_view(run_id: str) -> dict[str, Any]:
    """Assemble a read-only route-agent trace from Structured Run Memory."""
    from memory.run_memory import (
        _read_json,
        _read_jsonl,
        run_dir,
        run_normalized_dir,
        run_raw_dir,
        run_summary_dir,
    )

    if not run_dir(run_id).exists():
        raise FileNotFoundError(f"Run directory not found for run_id={run_id}")

    raw_dir = run_raw_dir(run_id)
    normalized_dir = run_normalized_dir(run_id)
    summary_dir = run_summary_dir(run_id)

    case_state = _read_json(normalized_dir / "case_state.json", {}) or {}
    case_timeline = _read_json(normalized_dir / "case_timeline.json", []) or []
    events = _read_jsonl(raw_dir / "uipath_execution_events.jsonl")
    http_calls = _read_jsonl(raw_dir / "http_calls.jsonl")
    erp_ui_actions = _read_jsonl(raw_dir / "erp_ui_actions.jsonl")

    erp_fields_artifact = _read_json(raw_dir / "rpa_extracted_fields.json", {}) or {}
    agent_route_artifact = _read_json(raw_dir / "agent_route_response.json", {}) or {}
    policy_response_artifact = _read_json(raw_dir / "policy_gate_response.json", {}) or {}
    agent_io_artifact = _read_json(raw_dir / "agent_input_output.json", {}) or {}
    route_plan_artifact = _read_json(raw_dir / "route_plan.json", {}) or {}
    company_context_artifact = _read_json(raw_dir / "company_context_snapshot.json", {}) or {}
    agent_reasoning_artifact = _read_json(raw_dir / "agent_reasoning_summary.json", {}) or {}
    llm_proof_artifact = _read_json(raw_dir / "llm_validation_proof.json", {}) or {}
    policy_gate_artifact = _read_json(raw_dir / "policy_gate.json", {}) or {}
    selected_action_artifact = _read_json(raw_dir / "selected_erp_action.json", {}) or {}
    approval_task_artifact = _read_json(raw_dir / "approval_task.json", {}) or {}
    final_branch_artifact = _read_json(raw_dir / "final_branch_result.json", {}) or {}
    case_run_summary = _read_json(summary_dir / "case_run_summary.json", {}) or {}
    post_run_summary = _read_json(summary_dir / "post_run_memory_summary.json", {}) or {}

    erp_fields = _artifact_data(erp_fields_artifact)
    agent_route_raw = _artifact_data(agent_route_artifact)
    policy_response_raw = _artifact_data(policy_response_artifact)
    agent_io = _artifact_data(agent_io_artifact)
    route_plan = _artifact_data(route_plan_artifact)
    company_context_from_file = _artifact_data(company_context_artifact)
    agent_reasoning = _artifact_data(agent_reasoning_artifact)
    llm_proof_from_file = _artifact_data(llm_proof_artifact)
    policy_gate_from_file = _artifact_data(policy_gate_artifact)
    selected_action_from_file = _artifact_data(selected_action_artifact)
    approval_task = _artifact_data(approval_task_artifact)
    final_branch = _artifact_data(final_branch_artifact)

    decoded_route_response, decoded_route_text, decoded_route_error = (
        _decode_base64_json_response(agent_route_raw)
    )
    decoded_policy_response, decoded_policy_text, decoded_policy_error = (
        _decode_base64_json_response(policy_response_raw)
    )
    route_response = decoded_route_response if isinstance(decoded_route_response, dict) else {}
    policy_response = decoded_policy_response if isinstance(decoded_policy_response, dict) else {}
    agent_response = (
        agent_io.get("response")
        if isinstance(agent_io.get("response"), dict)
        else {}
    )
    agent_request = (
        agent_io.get("request")
        if isinstance(agent_io.get("request"), dict)
        else {}
    )
    if not agent_request:
        agent_request = {
            "case_id": _first_present(erp_fields.get("case_id"), case_state.get("case_id")),
            "po_id": _first_present(erp_fields.get("po_id"), case_state.get("po_id")),
            "amount": erp_fields.get("amount"),
            "budget_limit": erp_fields.get("budget_limit"),
            "vendor_id": erp_fields.get("vendor_id"),
            "scenario": _first_present(erp_fields.get("scenario"), route_response.get("scenario")),
            "exception_reason": _first_present(
                erp_fields.get("exception_reason"),
                route_response.get("exception_reason"),
            ),
            "business_remarks": _first_present(
                erp_fields.get("business_remarks"),
                route_response.get("business_remarks"),
                route_plan.get("business_remarks"),
            ),
            "agent_context_policy": _first_present(
                route_response.get("agent_context_policy"),
                route_plan.get("agent_context_policy"),
            ),
        }
        agent_request = {k: v for k, v in agent_request.items() if v is not None}

    llm_proof = _first_dict(
        llm_proof_from_file,
        route_response.get("llm_validation_proof"),
        route_plan.get("llm_validation_proof"),
        case_run_summary.get("llm_validation_proof"),
    )
    if not llm_proof and (
        route_plan.get("llm_call_mode") or route_plan.get("llm_invocation_verified") is not None
    ):
        llm_proof = {
            "llm_call_mode": route_plan.get("llm_call_mode"),
            "llm_invocation_verified": route_plan.get("llm_invocation_verified"),
        }

    policy_gate = _first_dict(
        policy_gate_from_file,
        policy_response if policy_response.get("policy_decision") else {},
        route_response.get("policy_gate"),
        route_plan.get("policy_gate"),
        case_run_summary.get("policy_gate"),
    )
    recommended_action = _first_dict(
        selected_action_from_file,
        route_response.get("recommended_erp_action"),
        route_plan.get("recommended_erp_action"),
        case_run_summary.get("selected_erp_action"),
    )
    company_context = _first_dict(
        company_context_from_file,
        route_response.get("company_context_snapshot"),
        route_plan.get("company_context_snapshot"),
        case_run_summary.get("company_context_snapshot"),
    )
    context_reference = _first_dict(
        route_response.get("company_context_reference"),
        route_plan.get("company_context_reference"),
    )

    enterprise_context_source = _first_present(
        route_response.get("enterprise_context_source"),
        route_plan.get("enterprise_context_source"),
        company_context.get("enterprise_context_source"),
        default="not_captured",
    )
    enterprise_context_endpoint = _first_present(
        route_response.get("company_context_endpoint"),
        route_plan.get("company_context_endpoint"),
        company_context.get("company_context_endpoint"),
        company_context.get("enterprise_context_provider"),
        default="injected enterprise context snapshot",
    )
    route_agent_mode = _first_present(
        route_response.get("route_agent_mode"),
        route_plan.get("route_agent_mode"),
        default="not_captured",
    )
    route_endpoint = _first_present(
        agent_route_raw.get("endpoint"),
        route_plan.get("route_endpoint"),
        default="POST http://localhost:8002/case-intake/route",
    )
    policy_endpoint = "POST http://localhost:8002/policy-gate/evaluate"
    policy_fallback_observed = any(
        str(call.get("data", {}).get("endpoint") or call.get("endpoint") or "").endswith("/policy-gate/evaluate")
        or "/policy-gate/evaluate" in str(call)
        for call in http_calls
    )
    route_response_has_policy_gate = isinstance(route_response.get("policy_gate"), dict) and bool(
        route_response.get("policy_gate")
    )

    case_id = _first_present(
        case_state.get("case_id"),
        erp_fields.get("case_id"),
        route_response.get("case_id"),
        route_plan.get("case_id"),
        agent_route_artifact.get("case_id"),
        default="",
    )
    po_id = _first_present(
        case_state.get("po_id"),
        erp_fields.get("po_id"),
        route_response.get("po_id"),
        route_plan.get("po_id"),
        default="",
    )

    parsed_decision = {
        "final_route": _first_present(route_response.get("final_route"), route_plan.get("final_route")),
        "detected_exception_type": _first_present(
            route_response.get("detected_exception_type"),
            route_plan.get("detected_exception_type"),
            agent_response.get("detected_exception_type"),
        ),
        "capability_decision": _first_present(
            route_response.get("capability_decision"),
            route_plan.get("capability_decision"),
        ),
        "policy_decision": _first_present(
            route_response.get("policy_decision"),
            route_plan.get("policy_decision"),
            policy_gate.get("policy_decision"),
        ),
        "business_action": _first_present(
            route_response.get("business_action"),
            route_plan.get("business_action"),
            agent_response.get("business_action"),
            case_run_summary.get("business_action"),
        ),
        "recommended_erp_action": recommended_action,
        "human_required": _first_present(
            route_response.get("human_required"),
            policy_gate.get("human_required"),
        ),
        "execution_allowed": _first_present(
            route_response.get("execution_allowed"),
            policy_gate.get("execution_allowed"),
        ),
        "validation_required": policy_gate.get("validation_required"),
        "blocked_actions": policy_gate.get("blocked_actions", []),
        "route_agent_mode": route_agent_mode,
        "enterprise_context_source": enterprise_context_source,
        "llm_call_mode": _first_present(llm_proof.get("llm_call_mode"), route_plan.get("llm_call_mode")),
        "llm_invocation_verified": _first_present(
            llm_proof.get("llm_invocation_verified"),
            route_plan.get("llm_invocation_verified"),
        ),
    }

    completed = bool(final_branch) or case_state.get("status") == "RUN_COMPLETED"
    committed = bool(case_run_summary or post_run_summary)
    trace_steps = [
        {
            "name": "UiPath run started",
            "system": "UiPath -> Run Memory",
            "endpoint": "POST /memory/runs/start",
            "evidence": "uipath_execution_events.jsonl",
            "status": "captured" if events else "missing",
        },
        {
            "name": "ERP screen fields extracted",
            "system": "UiPath -> Legacy ERP",
            "endpoint": "ERP UI selectors",
            "evidence": "rpa_extracted_fields.json",
            "status": "captured" if erp_fields else "missing",
        },
        {
            "name": "Route agent decision requested",
            "system": "UiPath -> Reasoning Agent",
            "endpoint": route_endpoint,
            "evidence": "agent_route_response.json or route_plan.json",
            "status": "captured" if (route_response or route_plan) else "missing",
        },
        {
            "name": "Enterprise context loaded",
            "system": "Reasoning Agent",
            "endpoint": enterprise_context_endpoint,
            "evidence": enterprise_context_source,
            "status": "captured" if enterprise_context_source != "not_captured" else "missing",
        },
        {
            "name": "Agent decision proof captured",
            "system": "Reasoning Agent",
            "endpoint": str(
                llm_proof.get("llm_provider")
                or llm_proof.get("decision_source")
                or "injected demo decision"
            ),
            "evidence": "llm_validation_proof",
            "status": "captured" if llm_proof else "missing",
        },
        {
            "name": "Policy gate evaluated",
            "system": "Reasoning Agent Governance",
            "endpoint": (
                policy_endpoint
                if policy_fallback_observed
                else "route response policy_gate"
            ),
            "evidence": policy_gate.get("policy_decision") or "policy_gate.json",
            "status": "captured" if policy_gate else "missing",
        },
        {
            "name": "Memory closure and process evidence",
            "system": "UiPath -> Run Memory",
            "endpoint": "POST /memory/runs/{run_id}/complete + commit",
            "evidence": "case_run_summary.json" if committed else "run completion artifact",
            "status": "captured" if completed or committed else "missing",
        },
    ]

    artifact_status = {
        "rpa_extracted_fields.json": bool(erp_fields_artifact),
        "agent_route_response.json": bool(agent_route_artifact),
        "route_plan.json": bool(route_plan_artifact),
        "policy_gate_response.json": bool(policy_response_artifact),
        "policy_gate.json": bool(policy_gate_artifact),
        "company_context_snapshot.json": bool(company_context_artifact),
        "llm_validation_proof.json": bool(llm_proof_artifact),
        "selected_erp_action.json": bool(selected_action_artifact),
        "approval_task.json": bool(approval_task_artifact),
        "final_branch_result.json": bool(final_branch_artifact),
    }

    return {
        "run_id": run_id,
        "case_id": case_id,
        "po_id": po_id,
        "case_state": case_state,
        "case_timeline": case_timeline,
        "events": events,
        "http_calls": http_calls,
        "erp_ui_actions": erp_ui_actions,
        "erp_fields": erp_fields,
        "agent_request": agent_request,
        "agent_response": agent_response,
        "agent_route_raw": agent_route_raw,
        "decoded_route_response": route_response,
        "decoded_route_text": decoded_route_text,
        "decoded_route_error": decoded_route_error,
        "policy_response_raw": policy_response_raw,
        "decoded_policy_response": policy_response,
        "decoded_policy_text": decoded_policy_text,
        "decoded_policy_error": decoded_policy_error,
        "route_plan": route_plan,
        "company_context": company_context,
        "context_reference": context_reference,
        "agent_reasoning": agent_reasoning,
        "llm_proof": llm_proof,
        "policy_gate": policy_gate,
        "recommended_action": recommended_action,
        "approval_task": approval_task,
        "final_branch": final_branch,
        "case_run_summary": case_run_summary,
        "post_run_summary": post_run_summary,
        "parsed_decision": parsed_decision,
        "trace_steps": trace_steps,
        "artifact_status": artifact_status,
        "route_endpoint": route_endpoint,
        "policy_endpoint": policy_endpoint,
        "policy_fallback_observed": policy_fallback_observed,
        "route_response_has_policy_gate": route_response_has_policy_gate,
    }


def _render_agent_trace_html(data: dict[str, Any]) -> str:
    """Render a focused UiPath -> Agent trace page."""

    def _esc(val: Any) -> str:
        return html_lib.escape(str(val)) if val is not None else ""

    def _fmt(value: Any) -> str:
        if value is None or value == "":
            return '<span class="muted">Not captured</span>'
        if isinstance(value, bool):
            cls = "ok" if value else "blocked"
            return f'<span class="status {cls}">{"Yes" if value else "No"}</span>'
        if isinstance(value, (list, tuple)):
            return _esc(", ".join(str(v) for v in value) if value else "None")
        return _esc(value)

    def _json_block(obj: Any, title: str) -> str:
        if not obj:
            return '<span class="muted">Not captured</span>'
        text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
        return f"<details><summary>{_esc(title)}</summary><pre>{_esc(text)}</pre></details>"

    def _rows(mapping: dict[str, Any], labels: list[tuple[str, str]]) -> str:
        return "".join(
            f"<tr><th>{_esc(label)}</th><td>{_fmt(mapping.get(key))}</td></tr>"
            for key, label in labels
        )

    run_id = data.get("run_id", "")
    case_id = data.get("case_id", "")
    po_id = data.get("po_id", "")
    parsed = data.get("parsed_decision", {}) or {}
    erp_fields = data.get("erp_fields", {}) or {}
    llm_proof = data.get("llm_proof", {}) or {}
    policy_gate = data.get("policy_gate", {}) or {}
    company_context = data.get("company_context", {}) or {}
    context_reference = data.get("context_reference", {}) or {}
    route_plan = data.get("route_plan", {}) or {}
    route_response = data.get("decoded_route_response", {}) or {}
    policy_response = data.get("decoded_policy_response", {}) or {}
    recommended_action = data.get("recommended_action", {}) or {}

    case_dashboard = (
        f"/case-dashboard/{urllib.parse.quote(str(case_id), safe='')}?run_id={urllib.parse.quote(str(run_id), safe='')}"
        if case_id else f"/memory/runs/{urllib.parse.quote(str(run_id), safe='')}"
    )

    step_rows = "".join(
        f"<tr><td>{idx}</td><td><strong>{_esc(step['name'])}</strong><br><span>{_esc(step['system'])}</span></td>"
        f"<td>{_esc(step['endpoint'])}</td><td>{_esc(step['evidence'])}</td>"
        f"<td><span class='status {'ok' if step['status'] == 'captured' else 'warn'}'>{_esc(step['status'])}</span></td></tr>"
        for idx, step in enumerate(data.get("trace_steps", []), start=1)
    )
    artifact_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td><span class='status {'ok' if captured else 'warn'}'>{'Captured' if captured else 'Missing'}</span></td></tr>"
        for name, captured in (data.get("artifact_status", {}) or {}).items()
    )
    http_rows = "".join(
        f"<tr><td>{_esc(call.get('occurred_at', ''))}</td>"
        f"<td>{_esc((call.get('data') or {}).get('method') or call.get('method') or '')}</td>"
        f"<td>{_esc((call.get('data') or {}).get('endpoint') or call.get('endpoint') or '')}</td>"
        f"<td>{_esc((call.get('data') or {}).get('status_code') or call.get('status_code') or '')}</td></tr>"
        for call in data.get("http_calls", [])
    ) or "<tr><td colspan='4' class='muted'>No http_call artifact captured.</td></tr>"

    route_decode_note = data.get("decoded_route_error") or (
        "Decoded from response_json_base64." if data.get("decoded_route_text") else "No base64 route response captured."
    )
    policy_decode_note = data.get("decoded_policy_error") or (
        "Decoded from response_json_base64." if data.get("decoded_policy_text") else "No base64 policy response captured."
    )
    policy_source = (
        "Fallback /policy-gate/evaluate observed"
        if data.get("policy_fallback_observed")
        else (
            "policy_gate returned inside /case-intake/route"
            if data.get("route_response_has_policy_gate")
            else "policy_gate artifact only"
        )
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Trace - {run_id}</title>
<style>
  :root {{
    --bg: #eef2f7;
    --surface: #ffffff;
    --surface-2: #f8fafc;
    --line: #b8c3d1;
    --line-soft: #d9e0ea;
    --text: #172033;
    --muted: #5b697a;
    --blue: #0f5f9f;
    --green: #1f7a3f;
    --amber: #8a6100;
    --red: #9a2d2d;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: Tahoma, Verdana, Arial, sans-serif;
    font-size: 12px;
    line-height: 1.4;
  }}
  a {{ color: #004b9b; }}
  header {{
    border-bottom: 1px solid #8da0b8;
    background: #17283d;
    color: #fff;
    padding: 12px 16px;
  }}
  header h1 {{ margin: 0; font-size: 19px; letter-spacing: 0; }}
  header p {{ margin: 4px 0 0; color: #d8e2ef; }}
  .top-links {{ margin-top: 8px; display: flex; flex-wrap: wrap; gap: 8px; }}
  .top-links a {{
    min-height: 28px;
    padding: 5px 10px;
    border: 1px solid #8da0b8;
    background: #f8fafc;
    color: #12314f;
    text-decoration: none;
    font-weight: 700;
  }}
  main {{
    display: grid;
    grid-template-columns: repeat(12, minmax(0, 1fr));
    gap: 10px;
    padding: 10px;
  }}
  section {{
    background: var(--surface);
    border: 1px solid var(--line);
    min-width: 0;
  }}
  h2 {{
    margin: 0;
    padding: 7px 9px;
    border-bottom: 1px solid var(--line);
    background: #dbe5f0;
    color: #16314f;
    font-size: 13px;
  }}
  .body {{ padding: 9px; }}
  .span-4 {{ grid-column: span 4; }}
  .span-5 {{ grid-column: span 5; }}
  .span-6 {{ grid-column: span 6; }}
  .span-7 {{ grid-column: span 7; }}
  .span-8 {{ grid-column: span 8; }}
  .full {{ grid-column: 1 / -1; }}
  .kpis {{
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: repeat(6, minmax(120px, 1fr));
    gap: 8px;
  }}
  .kpi {{
    background: var(--surface);
    border: 1px solid var(--line);
    padding: 8px 9px;
    min-height: 58px;
  }}
  .kpi b {{ display: block; color: #102a47; overflow-wrap: anywhere; }}
  .kpi span {{ color: var(--muted); font-size: 11px; }}
  table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
  th, td {{ padding: 6px 7px; border: 1px solid var(--line-soft); text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
  th {{ width: 34%; background: var(--surface-2); color: #334155; font-weight: 700; }}
  thead th {{ width: auto; background: #edf3f9; }}
  .trace-table td:first-child {{ width: 36px; text-align: center; color: var(--muted); }}
  .trace-table td:nth-child(2) {{ width: 26%; }}
  .trace-table span {{ color: var(--muted); }}
  .status {{
    display: inline-block;
    padding: 2px 7px;
    border: 1px solid var(--line);
    background: #f6f8fb;
    color: #334155;
    font-weight: 700;
  }}
  .status.ok {{ color: var(--green); background: #eef8f0; border-color: #9bc4a7; }}
  .status.warn {{ color: var(--amber); background: #fff8df; border-color: #d7bd73; }}
  .status.blocked {{ color: var(--red); background: #fff0f0; border-color: #dfa5a5; }}
  .muted {{ color: var(--muted); }}
  details {{ margin-top: 7px; }}
  summary {{ cursor: pointer; color: var(--blue); font-weight: 700; }}
  pre {{
    margin: 7px 0 0;
    padding: 8px;
    max-height: 360px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    border: 1px solid var(--line-soft);
    background: #f8fafc;
    font-size: 11px;
  }}
  .note {{ margin: 0 0 8px; color: var(--muted); }}
  footer {{ padding: 10px 16px; color: var(--muted); border-top: 1px solid var(--line); }}
  @media (max-width: 1100px) {{
    main {{ grid-template-columns: 1fr; }}
    .span-4, .span-5, .span-6, .span-7, .span-8, .full {{ grid-column: 1 / -1; }}
    .kpis {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
  }}
</style>
</head>
<body>
<header>
  <h1>Agent Trace - UiPath Route Decision</h1>
  <p>Run { _esc(run_id) } · Case { _esc(case_id or 'not captured') } · PO { _esc(po_id or 'not captured') }</p>
  <div class="top-links">
    <a href="{_esc(case_dashboard)}">Case Dashboard</a>
    <a href="/memory/runs/{_esc(run_id)}">Run Memory JSON</a>
    <a href="/simulation/dashboard">Pattern Memory Dashboard</a>
  </div>
</header>
<main>
  <div class="kpis">
    <div class="kpi"><b>{_esc(parsed.get('final_route') or 'Not captured')}</b><span>final_route</span></div>
    <div class="kpi"><b>{_esc(parsed.get('detected_exception_type') or 'Not captured')}</b><span>detected_exception_type</span></div>
    <div class="kpi"><b>{_esc(parsed.get('capability_decision') or 'Not captured')}</b><span>capability_decision</span></div>
    <div class="kpi"><b>{_esc(parsed.get('policy_decision') or 'Not captured')}</b><span>policy_decision</span></div>
    <div class="kpi"><b>{_esc(parsed.get('llm_call_mode') or 'Not captured')}</b><span>llm_call_mode</span></div>
    <div class="kpi"><b>{_esc(parsed.get('enterprise_context_source') or 'Not captured')}</b><span>enterprise_context_source</span></div>
  </div>

  <section class="full">
    <h2>Call Path</h2>
    <div class="body">
      <p class="note">Ordered from UiPath ERP extraction through route-agent decision, mock enterprise context, policy gate, and memory closure.</p>
      <table class="trace-table">
        <thead><tr><th>#</th><th>Step</th><th>Endpoint / Function</th><th>Evidence</th><th>Status</th></tr></thead>
        <tbody>{step_rows}</tbody>
      </table>
    </div>
  </section>

  <section class="span-5">
    <h2>UiPath ERP Fields</h2>
    <div class="body">
      <table>{_rows(erp_fields, [
          ('simulation_case_id', 'Simulation Case'),
          ('po_id', 'PO ID'),
          ('amount', 'Amount'),
          ('budget_limit', 'Budget Limit'),
          ('vendor_id', 'Vendor ID'),
          ('scenario', 'Scenario'),
          ('exception_reason', 'Exception Reason'),
          ('business_remarks', 'Business Remarks'),
          ('erp_status', 'ERP Status'),
      ])}</table>
      {_json_block(erp_fields, 'rpa_extracted_fields.json')}
    </div>
  </section>

  <section class="span-7">
    <h2>Agent Request</h2>
    <div class="body">
      <table>{_rows(data.get('agent_request', {}) or {}, [
          ('case_id', 'case_id'),
          ('po_id', 'po_id'),
          ('amount', 'amount'),
          ('budget_limit', 'budget_limit'),
          ('vendor_id', 'vendor_id'),
          ('scenario', 'scenario'),
          ('exception_reason', 'exception_reason'),
          ('business_remarks', 'business_remarks'),
          ('agent_context_policy', 'agent_context_policy'),
      ])}</table>
      {_json_block(data.get('agent_request'), 'Route request payload')}
    </div>
  </section>

  <section class="span-6">
    <h2>Mock Enterprise Context</h2>
    <div class="body">
      <table>
        <tr><th>Context Source</th><td>{_fmt(parsed.get('enterprise_context_source'))}</td></tr>
        <tr><th>Context Mode</th><td>{_fmt(company_context.get('enterprise_context_mode'))}</td></tr>
        <tr><th>Context Provider</th><td>{_fmt(company_context.get('enterprise_context_provider'))}</td></tr>
        <tr><th>Finance Policy Used</th><td>{_fmt(context_reference.get('finance_policy_used'))}</td></tr>
        <tr><th>Sales Context Used</th><td>{_fmt(context_reference.get('sales_context_used'))}</td></tr>
        <tr><th>Operations Context Used</th><td>{_fmt(context_reference.get('operations_context_used'))}</td></tr>
      </table>
      {_json_block(company_context, 'company_context_snapshot')}
      {_json_block(context_reference, 'company_context_reference')}
    </div>
  </section>

  <section class="span-6">
    <h2>LLM Proof</h2>
    <div class="body">
      <table>
        <tr><th>Route Agent Mode</th><td>{_fmt(parsed.get('route_agent_mode'))}</td></tr>
        <tr><th>Reasoning Mode</th><td>{_fmt(llm_proof.get('reasoning_mode'))}</td></tr>
        <tr><th>LLM Enabled</th><td>{_fmt(llm_proof.get('llm_enabled'))}</td></tr>
        <tr><th>LLM Call Mode</th><td>{_fmt(parsed.get('llm_call_mode'))}</td></tr>
        <tr><th>LLM Invocation Verified</th><td>{_fmt(parsed.get('llm_invocation_verified'))}</td></tr>
        <tr><th>Provider</th><td>{_fmt(llm_proof.get('llm_provider'))}</td></tr>
        <tr><th>Model</th><td>{_fmt(llm_proof.get('llm_model') or llm_proof.get('model'))}</td></tr>
        <tr><th>Request ID</th><td>{_fmt(llm_proof.get('llm_request_id'))}</td></tr>
      </table>
      {_json_block(llm_proof, 'llm_validation_proof')}
    </div>
  </section>

  <section class="span-6">
    <h2>Parsed Agent Fields</h2>
    <div class="body">
      <table>
        <tr><th>business_action</th><td>{_fmt(parsed.get('business_action'))}</td></tr>
        <tr><th>final_route</th><td>{_fmt(parsed.get('final_route'))}</td></tr>
        <tr><th>detected_exception_type</th><td>{_fmt(parsed.get('detected_exception_type'))}</td></tr>
        <tr><th>capability_decision</th><td>{_fmt(parsed.get('capability_decision'))}</td></tr>
        <tr><th>recommended_erp_action.action_id</th><td>{_fmt(recommended_action.get('action_id'))}</td></tr>
        <tr><th>human_required</th><td>{_fmt(parsed.get('human_required'))}</td></tr>
        <tr><th>execution_allowed</th><td>{_fmt(parsed.get('execution_allowed'))}</td></tr>
      </table>
      {_json_block(route_response or route_plan, 'Decoded route response / route_plan')}
    </div>
  </section>

  <section class="span-6">
    <h2>Policy Gate</h2>
    <div class="body">
      <p class="note">Source: {_esc(policy_source)}</p>
      <table>
        <tr><th>policy_decision</th><td>{_fmt(parsed.get('policy_decision'))}</td></tr>
        <tr><th>execution_allowed</th><td>{_fmt(policy_gate.get('execution_allowed'))}</td></tr>
        <tr><th>human_required</th><td>{_fmt(policy_gate.get('human_required'))}</td></tr>
        <tr><th>validation_required</th><td>{_fmt(policy_gate.get('validation_required'))}</td></tr>
        <tr><th>blocked_actions</th><td>{_fmt(policy_gate.get('blocked_actions'))}</td></tr>
      </table>
      {_json_block(policy_gate or policy_response, 'policy_gate')}
    </div>
  </section>

  <section class="span-4">
    <h2>ERP Action / Approval</h2>
    <div class="body">
      {_json_block(recommended_action, 'recommended_erp_action')}
      {_json_block(data.get('approval_task'), 'approval_task')}
      {_json_block(data.get('final_branch'), 'final_branch_result')}
    </div>
  </section>

  <section class="span-4">
    <h2>Artifact Coverage</h2>
    <div class="body">
      <table>{artifact_rows}</table>
    </div>
  </section>

  <section class="span-4">
    <h2>Raw Response Decode</h2>
    <div class="body">
      <table>
        <tr><th>agent_route_response</th><td>{_esc(route_decode_note)}</td></tr>
        <tr><th>policy_gate_response</th><td>{_esc(policy_decode_note)}</td></tr>
      </table>
      {_json_block(data.get('agent_route_raw'), 'agent_route_response artifact')}
      {_json_block(data.get('policy_response_raw'), 'policy_gate_response artifact')}
    </div>
  </section>

  <section class="full">
    <h2>HTTP Calls Captured By UiPath</h2>
    <div class="body">
      <table>
        <thead><tr><th>Time</th><th>Method</th><th>Endpoint</th><th>Status</th></tr></thead>
        <tbody>{http_rows}</tbody>
      </table>
    </div>
  </section>

  <section class="span-6">
    <h2>Route Plan Artifact</h2>
    <div class="body">{_json_block(route_plan, 'route_plan.json')}</div>
  </section>

  <section class="span-6">
    <h2>Run Memory Summary</h2>
    <div class="body">
      {_json_block(data.get('case_run_summary'), 'case_run_summary.json')}
      {_json_block(data.get('post_run_summary'), 'post_run_memory_summary.json')}
    </div>
  </section>
</main>
<footer>Read-only Agent Trace generated from Structured Run Memory. It does not call an LLM, execute ERP actions, modify XAML, or write memory.</footer>
</body>
</html>"""


@app.get("/agent-trace/{run_id}", response_class=HTMLResponse)
def agent_trace(run_id: str) -> HTMLResponse:
    """Render the UiPath -> route-agent trace for one real run."""
    try:
        data = _build_agent_trace_view(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTMLResponse(content=_render_agent_trace_html(data))


@app.get("/demo/agent-context-trace", tags=["Demo"])
def demo_agent_context_trace() -> RedirectResponse:
    """Stable recording shortcut for the injected Agent + enterprise context trace."""
    run_id = "RUN-DEMO-AGENT-CONTEXT-001"
    from memory.run_memory import run_dir

    if not run_dir(run_id).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Injected demo trace is not seeded. Run the demo-state seed step "
                "or open /agent-trace/{run_id} for an existing run."
            ),
        )
    return RedirectResponse(
        url=f"/agent-trace/{run_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/case-dashboard/{case_id}", response_class=HTMLResponse)
def case_dashboard(
    case_id: str,
    run_id: str | None = None,
) -> HTMLResponse:
    """Render the Case Dashboard HTML page.

    When a ``run_id`` query parameter is supplied (or found in
    ``cases/{case_id}/latest_run_id.txt``), the dashboard renders **real run
    memory** from ``memory/runs/{run_id}/`` — including the live event log,
    raw RPA trace, agent I/O, HTTP calls, memory files written, pattern
    update (before/after), capability-evolution decision with rule evaluation,
    and proposal lifecycle.

    When no real run is available, the dashboard falls back to the static demo
    data for CASE-001/002/003 (backward compatible with existing screencasts).
    """
    resolved_run_id = _resolve_run_id(case_id, run_id)
    if resolved_run_id:
        try:
            from memory.run_memory import run_dir
            if run_dir(resolved_run_id).exists():
                data = _build_run_memory_dashboard(case_id, resolved_run_id)
                return HTMLResponse(
                    content=_render_run_memory_html(case_id, data)
                )
        except Exception as exc:
            # Fall through to static dashboard if real-run rendering fails.
            pass

    # Fallback: static demo dashboard (backward compatible).
    if case_id == "CASE-001":
        data = _build_case_001_dashboard()
    elif case_id in _DASHBOARD_STATIC:
        data = _DASHBOARD_STATIC[case_id]
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dashboard not available for case {case_id}. "
            "Supported: CASE-000, CASE-001, CASE-002, CASE-003, CASE-004.",
        )
    return HTMLResponse(content=_render_dashboard_html(case_id, data))


# ===========================================================================
# Case Portfolio / Routing Overview
#
# Aggregates CASE-000 / 001 / 002 / 003 into a single dashboard so the demo
# can show that the system is a UiPath-governed case routing layer, not a
# single PO-1001 script. Data source priority per case:
#   1. real run memory (cases/{case_id}/latest_run_id.txt -> runs/{run_id}/)
#   2. static fallback (_DASHBOARD_STATIC / _build_case_001_dashboard)
# A missing run memory for any case must NOT cause a 500 — the portfolio
# always renders all 4 cases.
# ===========================================================================

# Static portfolio metadata per case (used when no real run memory exists).
# Values reflect the canonical demo routing for each PO.
_PORTFOLIO_STATIC: dict[str, dict[str, Any]] = {
    "CASE-000": {
        "case_id": "CASE-000",
        "po_id": "PO-1000",
        "case_type": "normal",
        "exception_type": "none",
        "route": "STANDARD_PROCESSING",
        "agent_required": False,
        "human_required": False,
        "evolution_decision": "NO_EVOLUTION_REQUIRED",
        "execution_mode": "STANDARD",
        "next_stage": "STANDARD_PROCESSING",
    },
    "CASE-001": {
        "case_id": "CASE-001",
        "po_id": "PO-1001",
        "case_type": "exception",
        "exception_type": "budget_exceeded",
        "route": "WAITING_FOR_HUMAN_APPROVAL -> API_MODE_EXECUTED",
        "agent_required": True,
        "human_required": True,
        "evolution_decision": "USE_TRUSTED_CAPABILITY",
        "execution_mode": "API",
        "next_stage": "API_MODE_EXECUTED",
    },
    "CASE-002": {
        "case_id": "CASE-002",
        "po_id": "PO-1002",
        "case_type": "exception",
        "exception_type": "vendor_info_missing",
        "route": "WAITING_VENDOR_INFO",
        "agent_required": True,
        "human_required": True,
        "evolution_decision": "WAIT_FOR_VENDOR_INFO",
        "execution_mode": "RPA",
        "next_stage": "WAITING_VENDOR_INFO",
    },
    "CASE-003": {
        "case_id": "CASE-003",
        "po_id": "PO-1003",
        "case_type": "capability_gap",
        "exception_type": "inventory_shortage",
        "route": "CAPABILITY_GAP_DETECTED",
        "agent_required": True,
        "human_required": True,
        "evolution_decision": "XAML_WORKFLOW_PROPOSAL",
        "execution_mode": "RPA",
        "next_stage": "CAPABILITY_GAP_DETECTED",
    },
    "CASE-004": {
        "case_id": "CASE-004",
        "po_id": "PO-1004",
        "case_type": "ambiguous",
        "exception_type": "unknown_exception",
        "route": "WAITING_MANUAL_INVESTIGATION",
        "agent_required": True,
        "human_required": True,
        "evolution_decision": "MANUAL_INVESTIGATION",
        "execution_mode": "NONE",
        "next_stage": "WAITING_MANUAL_INVESTIGATION",
    },
}

_PORTFOLIO_CASE_ORDER = ("CASE-000", "CASE-001", "CASE-002", "CASE-003", "CASE-004")


def _build_portfolio_row(case_id: str) -> dict[str, Any]:
    """Build a single portfolio row for ``case_id``.

    Priority:
      1. Real run memory (if ``cases/{case_id}/latest_run_id.txt`` exists and
         the run directory is present).
      2. Static fallback from ``_PORTFOLIO_STATIC``.

    Never raises — on any error falls back to the static row.
    """
    static = dict(_PORTFOLIO_STATIC.get(case_id, {}))
    run_id = _resolve_run_id(case_id, None)
    row: dict[str, Any] = {
        "case_id": case_id,
        "po_id": static.get("po_id", "—"),
        "case_type": static.get("case_type", "unknown"),
        "exception_type": static.get("exception_type", "—"),
        "route": static.get("route", "—"),
        "agent_required": static.get("agent_required", False),
        "human_required": static.get("human_required", False),
        "evolution_decision": static.get("evolution_decision", "—"),
        "execution_mode": static.get("execution_mode", "—"),
        "latest_run_id": run_id,
        "data_source": "static_fallback",
    }

    if not run_id:
        return row

    try:
        from memory.run_memory import run_dir, _read_json
        rdir = run_dir(run_id)
        if not rdir.exists():
            return row

        # Read normalized case_state + evolution decision from run memory.
        norm_dir = rdir / "normalized"
        evo_dir = rdir / "evolution"
        case_state = _read_json(norm_dir / "case_state.json", {}) or {}
        decision = _read_json(evo_dir / "capability_evolution_decision.json", {}) or {}
        process_sig = _read_json(norm_dir / "process_signature.json", {}) or {}

        # Derive fields from real run memory.
        exception_type = "none"
        sig = process_sig.get("process_signature") or ""
        if "__" in sig:
            exception_type = sig.split("__", 1)[1]

        decision_label = decision.get("decision") or static.get("evolution_decision", "—")
        # CASE-001 real runs may produce API_MODERNIZATION_PROPOSAL; keep the
        # canonical routing label for the route column.
        route = static.get("route", "—")
        if case_state.get("final_stage"):
            route = f"{static.get('route', route)}"

        row.update({
            "po_id": case_state.get("po_id") or static.get("po_id", "—"),
            "case_type": static.get("case_type", "unknown"),
            "exception_type": exception_type,
            "route": route,
            "evolution_decision": decision_label,
            "execution_mode": case_state.get("execution_mode") or static.get("execution_mode", "—"),
            "latest_run_id": run_id,
            "data_source": "real_run_memory",
            "requires_human_approval": decision.get("requires_human_approval"),
            "auto_execution_allowed": decision.get("auto_execution_allowed"),
        })
    except Exception:
        # Keep static row on any error — portfolio must never 500.
        pass

    return row


def _render_portfolio_html(rows: list[dict[str, Any]]) -> str:
    """Render the Case Portfolio / Routing Overview page."""
    import html as html_lib

    def _esc(val: Any) -> str:
        return html_lib.escape(str(val)) if val is not None else ""

    # Summary counts.
    total = len(rows)
    normal = sum(1 for r in rows if r.get("case_type") == "normal")
    exception = sum(1 for r in rows if r.get("case_type") == "exception")
    waiting = sum(1 for r in rows if "WAITING" in str(r.get("route", "")))
    capability_gap = sum(1 for r in rows if r.get("case_type") == "capability_gap")

    # Governance signals (aggregate across cases).
    any_human_gate = any(r.get("human_required") for r in rows)
    any_validation_gate = any(
        r.get("evolution_decision") in {"API_MODERNIZATION_PROPOSAL", "USE_TRUSTED_CAPABILITY"}
        for r in rows
    )
    any_proposal = any(
        "PROPOSAL" in str(r.get("evolution_decision", ""))
        for r in rows
    )
    # Trusted registry: read once.
    try:
        from memory.store import read_json
        registry = read_json("capability_registry.json", {})
    except Exception:
        registry = {}
    trusted_count = 1 if registry else 0

    # Build case table rows with links.
    table_rows = ""
    for r in rows:
        case_id = r.get("case_id", "—")
        run_id = r.get("latest_run_id")
        if run_id:
            link = f"/case-dashboard/{case_id}?run_id={run_id}"
        else:
            link = f"/case-dashboard/{case_id}"
        case_type = r.get("case_type", "—")
        type_badge_class = {
            "normal": "badge-normal",
            "exception": "badge-exception",
            "capability_gap": "badge-gap",
            "ambiguous": "badge-ambiguous",
        }.get(case_type, "badge-unknown")
        agent_req = "✅ Yes" if r.get("agent_required") else "❌ No"
        human_req = "✅ Yes" if r.get("human_required") else "❌ No"
        data_source_badge = (
            '<span class="source-real">real run memory</span>'
            if r.get("data_source") == "real_run_memory"
            else '<span class="source-static">static fallback</span>'
        )
        table_rows += f"""
        <tr>
          <td><a href="{_esc(link)}"><strong>{_esc(case_id)}</strong></a></td>
          <td>{_esc(r.get('po_id', '—'))}</td>
          <td><span class="badge {type_badge_class}">{_esc(case_type)}</span></td>
          <td>{_esc(r.get('exception_type', '—'))}</td>
          <td>{_esc(r.get('route', '—'))}</td>
          <td>{agent_req}</td>
          <td>{human_req}</td>
          <td><strong>{_esc(r.get('evolution_decision', '—'))}</strong></td>
          <td>{_esc(r.get('execution_mode', '—'))}</td>
          <td>{_esc(run_id or '—')}</td>
          <td>{data_source_badge}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Case Portfolio / Routing Overview — UiPath Governance Layer</title>
<style>
  :root {{
    --bg: #0f172a; --panel: #1e293b; --panel-2: #273449;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
    --green: #4ade80; --amber: #fbbf24; --red: #f87171; --purple: #c084fc;
    --border: #334155;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); font-size: 13px; }}
  header {{ background: linear-gradient(90deg, #1e293b, #0f172a); padding: 18px 32px; border-bottom: 1px solid var(--border); }}
  header h1 {{ margin: 0; font-size: 22px; }}
  header .subtitle {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  main {{ padding: 24px 32px; display: grid; grid-template-columns: 1fr; gap: 20px; }}
  .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 18px; }}
  .panel h2 {{ margin: 0 0 12px 0; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--accent); }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }}
  .summary-card {{ background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px; padding: 14px; text-align: center; }}
  .summary-card .num {{ font-size: 28px; font-weight: 700; }}
  .summary-card .label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }}
  .summary-card.total .num {{ color: var(--accent); }}
  .summary-card.normal .num {{ color: var(--green); }}
  .summary-card.exception .num {{ color: var(--amber); }}
  .summary-card.waiting .num {{ color: var(--purple); }}
  .summary-card.gap .num {{ color: var(--red); }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; padding: 10px 8px; border-bottom: 2px solid var(--border); color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
  td {{ padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  td a {{ color: var(--accent); text-decoration: none; }}
  td a:hover {{ text-decoration: underline; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .badge-normal {{ background: rgba(74,222,128,0.18); color: var(--green); }}
  .badge-exception {{ background: rgba(251,191,36,0.18); color: var(--amber); }}
  .badge-gap {{ background: rgba(248,113,113,0.18); color: var(--red); }}
  .badge-ambiguous {{ background: rgba(192,132,252,0.18); color: var(--purple); }}
  .badge-unknown {{ background: rgba(148,163,184,0.18); color: var(--muted); }}
  .source-real {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; background: rgba(74,222,128,0.15); color: var(--green); }}
  .source-static {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; background: rgba(148,163,184,0.15); color: var(--muted); }}
  .signals {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
  .signal-card {{ background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }}
  .signal-card .title {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }}
  .signal-card .value {{ font-size: 18px; font-weight: 600; }}
  .signal-card .value.ok {{ color: var(--green); }}
  .signal-card .value.warn {{ color: var(--amber); }}
  .description {{ background: var(--panel-2); border-left: 3px solid var(--accent); padding: 14px 18px; border-radius: 6px; color: var(--text); line-height: 1.6; }}
  footer {{ padding: 16px 32px; color: var(--muted); font-size: 11px; border-top: 1px solid var(--border); }}
</style>
</head>
<body>
<header>
  <h1>Case Portfolio / Routing Overview</h1>
  <div class="subtitle">UiPath-governed case routing layer · {total} cases · Structured Memory is the source of truth</div>
</header>
<main>
  <section class="panel">
    <h2>Portfolio Summary</h2>
    <div class="summary-grid">
      <div class="summary-card total"><div class="num">{total}</div><div class="label">Total Cases</div></div>
      <div class="summary-card normal"><div class="num">{normal}</div><div class="label">Normal</div></div>
      <div class="summary-card exception"><div class="num">{exception}</div><div class="label">Exception</div></div>
      <div class="summary-card waiting"><div class="num">{waiting}</div><div class="label">Waiting</div></div>
      <div class="summary-card gap"><div class="num">{capability_gap}</div><div class="label">Capability Gap</div></div>
    </div>
  </section>

  <section class="panel">
    <h2>Case Table</h2>
    <table>
      <thead>
        <tr>
          <th>Case ID</th><th>PO ID</th><th>Case Type</th><th>Exception Type</th>
          <th>Route</th><th>Agent Required</th><th>Human Required</th>
          <th>Evolution Decision</th><th>Execution Mode</th><th>Latest Run ID</th><th>Source</th>
        </tr>
      </thead>
      <tbody>{table_rows}
      </tbody>
    </table>
  </section>

  <section class="panel">
    <h2>Governance Signals</h2>
    <div class="signals">
      <div class="signal-card">
        <div class="title">Human Approval Gate</div>
        <div class="value {'ok' if any_human_gate else 'warn'}">{'Active' if any_human_gate else 'None'}</div>
      </div>
      <div class="signal-card">
        <div class="title">Validation Gate</div>
        <div class="value {'ok' if any_validation_gate else 'warn'}">{'Active' if any_validation_gate else 'None'}</div>
      </div>
      <div class="signal-card">
        <div class="title">Proposal Lifecycle</div>
        <div class="value {'warn' if any_proposal else 'ok'}">{'Proposals pending' if any_proposal else 'No open proposals'}</div>
      </div>
      <div class="signal-card">
        <div class="title">Trusted Registry</div>
        <div class="value {'ok' if trusted_count else 'warn'}">{trusted_count} registered</div>
      </div>
    </div>
  </section>

  <section class="panel">
    <h2>How This System Routes Cases</h2>
    <div class="description">
      Normal cases use deterministic precheck; exception/ambiguous cases use Agent routing.
      All runs write structured memory (raw artifacts → normalized → summary → pattern update → evolution decision).
      Proposals move through review, validation, and registration before reuse.
      Proposed changes are not auto-executed.
    </div>
  </section>
</main>
<footer>Source: real Run Memory where available, static fallback otherwise · Structured Memory is the system of record</footer>
</body>
</html>"""


@app.get("/case-portfolio", response_class=HTMLResponse)
def case_portfolio() -> HTMLResponse:
    """Render the Case Portfolio / Routing Overview page.

    Aggregates CASE-000 / 001 / 002 / 003 into a single dashboard so the demo
    can show that the system is a UiPath-governed case routing layer (not a
    single PO-1001 script).

    For each case, the portfolio prefers real Run Memory
    (``cases/{case_id}/latest_run_id.txt`` -> ``runs/{run_id}/``) and falls
    back to static demo data when no run exists. A missing run for any case
    never causes a 500 — the portfolio always renders all 4 cases.
    """
    rows = [_build_portfolio_row(case_id) for case_id in _PORTFOLIO_CASE_ORDER]
    return HTMLResponse(content=_render_portfolio_html(rows))


# ===========================================================================
# Case Intake Router + Router Lab
#
# Unified intake endpoint that combines precheck -> triage -> capability
# decision into a single route plan for UiPath to call as one entry point.
# Side-effect free: does not write memory, does not execute business actions,
# does not modify XAML, does not deploy APIs, does not auto-上线.
# ===========================================================================

# Mapping from case_id to the recommended UiPath workflow file for the demo.
# These are display-only recommendations — the system never writes or modifies
# these XAML files.
_RECOMMENDED_UIPATH_WORKFLOW: dict[str, str] = {
    "CASE-000": "RouteProof_PO1000.xaml",
    "CASE-001": "Main.xaml",
    "CASE-002": "RouteProof_PO1002.xaml",
    "CASE-003": "RouteProof_PO1003.xaml",
    "CASE-004": "RouteProof_PO1004.xaml",
}

# For the capability-evolution decision we need a business_action per
# exception_type. The triage classifier already carries business_action in its
# response; this fallback map covers the normal/ambiguous cases and aligns
# with the seeded historical_patterns.json business_action values so the
# evaluator can find the matching pattern.
_EXCEPTION_TO_BUSINESS_ACTION: dict[str, str] = {
    "none": "standard_purchase_order_processing",
    "no_exception": "standard_purchase_order_processing",
    "budget_exceeded": "request_purchase_order_approval",
    "vendor_info_missing": "handle_vendor_info_missing",
    "inventory_shortage": "request_inventory_review",
    "unknown_exception": "manual_case_review",
}


_ERP_ROUTE_ACTIONS: dict[str, dict[str, str | None]] = {
    "STANDARD_PROCESSING": {
        "action_id": "STANDARD_PROCESSING",
        "button_selector_id": "ctl00_MainContent_btnMarkStandardProcessed",
        "reason": "Order can continue through standard ERP purchase order processing.",
    },
    "WAITING_VENDOR_INFO": {
        "action_id": "MARK_WAITING_VENDOR",
        "button_selector_id": "ctl00_MainContent_btnMarkWaitingVendor",
        "reason": "Vendor compliance data is missing.",
    },
    "CAPABILITY_GAP_DETECTED": {
        "action_id": "FLAG_CAPABILITY_GAP",
        "button_selector_id": "ctl00_MainContent_btnFlagCapabilityGap",
        "reason": "Current RPA/API capabilities do not cover the required review path.",
    },
    "WAITING_MANUAL_INVESTIGATION": {
        "action_id": "SEND_MANUAL_INVESTIGATION",
        "button_selector_id": "ctl00_MainContent_btnSendManualInvestigation",
        "reason": "Manual investigation is required before ERP execution.",
    },
    "WAITING_FOR_HUMAN_APPROVAL": {
        "action_id": "CREATE_WEB_APPROVAL_TASK",
        "button_selector_id": None,
        "reason": (
            "No ERP approval click is recommended; create a web approval task "
            "through /approvals/create."
        ),
    },
}


def _recommended_erp_action(final_route: str) -> dict[str, str | None]:
    return dict(
        _ERP_ROUTE_ACTIONS.get(
            final_route,
            {
                "action_id": "SEND_MANUAL_INVESTIGATION",
                "button_selector_id": "ctl00_MainContent_btnSendManualInvestigation",
                "reason": "Fallback route requires manual investigation.",
            },
        )
    )


def _build_route_plan(payload: TriageRequest) -> dict[str, Any]:
    """Build a unified route plan by composing precheck -> triage -> decision.

    This is a pure function: it does not write memory, call an LLM, modify
    XAML, or deploy APIs. It reuses the existing deterministic precheck, the
    triage classifier, and the capability-evolution evaluator.
    """
    case_id = payload.case_id
    po_id = payload.po_id

    # Step 1: deterministic precheck.
    precheck_result = precheck(payload)

    precheck_label = precheck_result.get("precheck_result", "AMBIGUOUS")
    case_type = precheck_result.get("case_type", "ambiguous")
    agent_required = precheck_result.get("agent_required", True)

    # Step 2: triage (only needed for CLEAR_EXCEPTION and AMBIGUOUS).
    triage_result: dict[str, Any] | None = None
    detected_exception_type = "none"
    confidence: float | None = None
    if precheck_label != "NORMAL":
        triage_response = classify_exception(payload)
        triage_result = triage_response.model_dump()
        detected_exception_type = triage_response.detected_exception_type
        confidence = triage_response.confidence

    # Step 3: derive final_route + next_stage from precheck + triage.
    if precheck_label == "NORMAL":
        final_route = "STANDARD_PROCESSING"
        next_stage = "STANDARD_PROCESSING"
    elif precheck_label == "CLEAR_EXCEPTION":
        # Use the triage next_stage as the route.
        final_route = triage_result.get("next_stage", "WAITING_FOR_TRIAGE") if triage_result else "WAITING_FOR_TRIAGE"
        next_stage = final_route
    else:  # AMBIGUOUS
        # Low-confidence ambiguous -> manual investigation.
        if confidence is not None and confidence < 0.75:
            final_route = "WAITING_MANUAL_INVESTIGATION"
            next_stage = "WAITING_MANUAL_INVESTIGATION"
        else:
            final_route = "AGENT_SEMANTIC_ROUTING"
            next_stage = "AGENT_SEMANTIC_ROUTING"

    # Step 4: capability evolution decision.
    business_action = (
        payload.business_action
        or _EXCEPTION_TO_BUSINESS_ACTION.get(
            detected_exception_type,
            triage_result.get("business_action") if triage_result else "manual_case_review",
        )
    )
    evolution_request = CapabilityEvolutionEvaluateRequest(
        case_id=case_id,
        po_id=po_id,
        exception_type=detected_exception_type,
        business_action=business_action,
    )
    capability_decision = evaluate_capability_evolution(evolution_request)
    decision_label = capability_decision.get("decision", "UNKNOWN")

    # Step 5: governance flags.
    human_required = (
        capability_decision.get("requires_human_approval")
        or capability_decision.get("requires_human_review")
        or (triage_result.get("requires_human_approval") if triage_result else False)
        or precheck_label != "NORMAL"
    )
    # execution_allowed = True only for normal standard path (no human gate).
    execution_allowed = precheck_label == "NORMAL" and not human_required
    auto_modernization_allowed = (
        decision_label == "API_MODERNIZATION_PROPOSAL"
        and capability_decision.get("coding_agent_allowed") == "after_approval_only"
    )

    # Recommended UiPath workflow (display-only; never written).
    recommended_workflow = _RECOMMENDED_UIPATH_WORKFLOW.get(case_id, "Main.xaml")

    # Reason text.
    if precheck_label == "NORMAL":
        reason = "Normal purchase order follows standard processing."
    elif precheck_label == "CLEAR_EXCEPTION":
        reason = (
            f"Deterministic exception ({detected_exception_type}) routes to "
            f"{final_route}; capability decision is {decision_label}."
        )
    else:
        reason = (
            f"Ambiguous case (confidence={confidence}); low-confidence cases go "
            f"to manual investigation. Capability decision is {decision_label}."
        )

    # Step 6: evaluate the governance policy gate (side-effect free).
    gate_request = PolicyGateRequest(
        case_id=case_id,
        po_id=po_id,
        case_type=case_type,
        precheck_result=precheck_label,
        detected_exception_type=detected_exception_type,
        confidence=confidence,
        final_route=final_route,
        capability_decision=decision_label,
        execution_mode=recommended_workflow,
    )
    policy_gate = _evaluate_policy_gate(gate_request)
    agent_context_used = False
    company_context_reference = {
        "finance_policy_used": False,
        "sales_context_used": False,
        "operations_context_used": False,
    }
    agent_reasoning_summary = reason
    llm_validation_proof = {
        "reasoning_mode": "deterministic_rule",
        "llm_enabled": False,
        "llm_call_mode": "not_invoked",
        "llm_provider": "not_invoked",
        "schema_validated": True,
        "guardrails_applied": True,
        "decision_status": "PRECHECK_ONLY" if precheck_label == "NORMAL" else "NOT_INVOKED",
        "llm_invocation_verified": False,
    }
    company_context_snapshot: dict[str, Any] | None = None
    policy_decision = policy_gate.get("policy_decision")

    if agent_required:
        company_context_snapshot = company_context_payload()
        agent_decision = run_route_agent(
            payload=payload,
            precheck_result=precheck_result,
            triage_result=triage_result,
            company_context=company_context_snapshot,
            fallback_route=final_route,
            fallback_policy=str(policy_decision or "REQUIRE_MANUAL_INVESTIGATION"),
        )
        final_route = str(agent_decision["final_route"])
        next_stage = final_route
        policy_gate = _evaluate_policy_gate(
            PolicyGateRequest(
                case_id=case_id,
                po_id=po_id,
                case_type=case_type,
                precheck_result=precheck_label,
                detected_exception_type=detected_exception_type,
                confidence=agent_decision.get("agent_confidence", confidence),
                final_route=final_route,
                capability_decision=decision_label,
                execution_mode=recommended_workflow,
            )
        )
        if agent_decision.get("policy_gate_decision"):
            policy_gate["policy_decision"] = agent_decision["policy_gate_decision"]
        policy_decision = policy_gate.get("policy_decision")
        agent_context_used = bool(agent_decision.get("agent_context_used"))
        company_context_reference = dict(agent_decision.get("company_context_reference") or {})
        agent_reasoning_summary = str(agent_decision.get("agent_reasoning_summary") or reason)
        llm_validation_proof = dict(agent_decision.get("llm_validation_proof") or llm_validation_proof)

    enterprise_context_source = (
        company_context_snapshot or {}
    ).get("enterprise_context_source", "not_used")
    llm_call_mode = str(llm_validation_proof.get("llm_call_mode") or "not_invoked")
    if agent_context_used and llm_call_mode in {"real", "mock"}:
        route_agent_mode = f"{llm_call_mode}_llm_with_{enterprise_context_source}"
    elif agent_context_used:
        route_agent_mode = f"llm_unverified_with_{enterprise_context_source}"
    else:
        route_agent_mode = "deterministic_precheck_no_enterprise_context"

    recommended_action = _recommended_erp_action(final_route)
    if final_route == "WAITING_FOR_HUMAN_APPROVAL":
        recommended_action["policy_gate_reason"] = policy_gate.get("reason")

    return {
        "case_id": case_id,
        "po_id": po_id,
        "scenario": payload.scenario,
        "exception_reason": payload.exception_reason,
        "business_remarks": payload.business_remarks or "",
        "agent_context_policy": payload.agent_context_policy,
        "enterprise_context_source": enterprise_context_source,
        "route_agent_mode": route_agent_mode,
        "business_action": business_action,
        "case_type": case_type,
        "precheck_result": precheck_label,
        "precheck_decision_source": "deterministic_rule",
        "agent_required": agent_required,
        "triage_result": triage_result,
        "detected_exception_type": detected_exception_type,
        "confidence": confidence,
        "final_route": final_route,
        "policy_decision": policy_decision,
        "next_stage": next_stage,
        "capability_decision": decision_label,
        "human_required": human_required,
        "execution_allowed": execution_allowed,
        "auto_modernization_allowed": auto_modernization_allowed,
        "recommended_uipath_workflow": recommended_workflow,
        "dashboard_url": f"http://localhost:8002/case-dashboard/{case_id}",
        "portfolio_url": "http://localhost:8002/case-portfolio",
        "reason": reason,
        # Governance policy gate (backward-compatible additive field).
        "policy_gate": policy_gate,
        "agent_context_used": agent_context_used,
        "company_context_reference": company_context_reference,
        "company_context_snapshot": company_context_snapshot,
        "agent_reasoning_summary": agent_reasoning_summary,
        "llm_validation_proof": llm_validation_proof,
        "recommended_erp_action": recommended_action,
    }


@app.post("/case-intake/route")
def case_intake_route(payload: TriageRequest) -> dict[str, Any]:
    """Unified case intake router.

    Combines deterministic precheck -> triage classification -> capability
    evolution decision into a single route plan. Side-effect free: does not
    write memory, does not execute business actions, does not modify XAML,
    does not deploy APIs, does not auto-上线.

    Flow:
      1. precheck -> NORMAL / CLEAR_EXCEPTION / AMBIGUOUS
      2. if not NORMAL: triage -> exception_type + confidence
      3. capability evolution decision (NO_EVOLUTION_REQUIRED /
         USE_TRUSTED_CAPABILITY / API_MODERNIZATION_PROPOSAL /
         WAIT_FOR_VENDOR_INFO / XAML_WORKFLOW_PROPOSAL /
         MANUAL_INVESTIGATION)
      4. return unified route plan
    """
    return _build_route_plan(payload)


# Static demo payloads for the 5 canonical cases (used by /case-router-lab).
_ROUTER_LAB_PAYLOADS: list[dict[str, Any]] = [
    {
        "case_id": "CASE-000", "po_id": "PO-1000", "amount": 6000,
        "budget_limit": 10000, "vendor_id": "V-100",
        "vendor_info_complete": True, "inventory_available": True,
        "erp_status": "Normal", "raw_exception_text": "",
    },
    {
        "case_id": "CASE-001", "po_id": "PO-1001", "amount": 18000,
        "budget_limit": 10000, "vendor_id": "V-203",
        "vendor_info_complete": True, "inventory_available": True,
        "erp_status": "Exception",
        "raw_exception_text": "Amount exceeds approved budget limit",
    },
    {
        "case_id": "CASE-002", "po_id": "PO-1002", "amount": 6000,
        "budget_limit": 10000, "vendor_id": None,
        "vendor_info_complete": False, "inventory_available": True,
        "erp_status": "Normal", "raw_exception_text": "Vendor information missing",
    },
    {
        "case_id": "CASE-003", "po_id": "PO-1003", "amount": 8500,
        "budget_limit": 10000, "vendor_id": "V-118",
        "vendor_info_complete": True, "inventory_available": False,
        "erp_status": "Normal", "raw_exception_text": "Inventory shortage",
    },
    {
        "case_id": "CASE-004", "po_id": "PO-1004", "amount": 9500,
        "budget_limit": 10000, "vendor_id": "V-404",
        "vendor_info_complete": True, "inventory_available": True,
        "erp_status": "PendingReview",
        "raw_exception_text": "Needs business attention before processing.",
    },
]


def _render_router_lab_html(plans: list[dict[str, Any]]) -> str:
    """Render the Case Router Lab page — a full route matrix for 5 cases."""
    import html as html_lib

    def _esc(val: Any) -> str:
        return html_lib.escape(str(val)) if val is not None else ""

    # Build table rows.
    table_rows = ""
    for p in plans:
        case_id = p.get("case_id", "—")
        precheck = p.get("precheck_result", "—")
        case_type = p.get("case_type", "—")
        exception_type = p.get("detected_exception_type", "—")
        confidence = p.get("confidence")
        confidence_str = f"{confidence:.2f}" if confidence is not None else "—"
        final_route = p.get("final_route", "—")
        decision = p.get("capability_decision", "—")
        workflow = p.get("recommended_uipath_workflow", "—")
        agent_req = "✅ Yes" if p.get("agent_required") else "❌ No"
        human_req = "✅ Yes" if p.get("human_required") else "❌ No"
        exec_allowed = "✅ Yes" if p.get("execution_allowed") else "❌ No"

        # Badges.
        precheck_badge_class = {
            "NORMAL": "badge-normal",
            "CLEAR_EXCEPTION": "badge-exception",
            "AMBIGUOUS": "badge-ambiguous",
        }.get(precheck, "badge-unknown")

        table_rows += f"""
        <tr>
          <td><a href="/case-dashboard/{_esc(case_id)}"><strong>{_esc(case_id)}</strong></a></td>
          <td>{_esc(p.get('po_id', '—'))}</td>
          <td><span class="badge {precheck_badge_class}">{_esc(precheck)}</span></td>
          <td>{_esc(case_type)}</td>
          <td>{agent_req}</td>
          <td>{_esc(exception_type)}</td>
          <td>{confidence_str}</td>
          <td><strong>{_esc(final_route)}</strong></td>
          <td>{_esc(decision)}</td>
          <td>{human_req}</td>
          <td>{exec_allowed}</td>
          <td><code>{_esc(workflow)}</code></td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Case Router Lab — Unified Route Matrix</title>
<style>
  :root {{
    --bg: #0f172a; --panel: #1e293b; --panel-2: #273449;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
    --green: #4ade80; --amber: #fbbf24; --red: #f87171; --purple: #c084fc;
    --border: #334155;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); font-size: 13px; }}
  header {{ background: linear-gradient(90deg, #1e293b, #0f172a); padding: 18px 32px; border-bottom: 1px solid var(--border); }}
  header h1 {{ margin: 0; font-size: 22px; }}
  header .subtitle {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  main {{ padding: 24px 32px; display: grid; grid-template-columns: 1fr; gap: 20px; }}
  .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 18px; }}
  .panel h2 {{ margin: 0 0 12px 0; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--accent); }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; padding: 10px 8px; border-bottom: 2px solid var(--border); color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; }}
  td {{ padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  td a {{ color: var(--accent); text-decoration: none; }}
  td a:hover {{ text-decoration: underline; }}
  code {{ background: var(--panel-2); padding: 2px 6px; border-radius: 3px; font-size: 11px; color: var(--green); }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; white-space: nowrap; }}
  .badge-normal {{ background: rgba(74,222,128,0.18); color: var(--green); }}
  .badge-exception {{ background: rgba(251,191,36,0.18); color: var(--amber); }}
  .badge-ambiguous {{ background: rgba(192,132,252,0.18); color: var(--purple); }}
  .badge-unknown {{ background: rgba(148,163,184,0.18); color: var(--muted); }}
  .description {{ background: var(--panel-2); border-left: 3px solid var(--accent); padding: 14px 18px; border-radius: 6px; color: var(--text); line-height: 1.6; }}
  .api-hint {{ background: var(--panel-2); border: 1px solid var(--border); border-radius: 6px; padding: 12px 16px; font-family: monospace; font-size: 12px; color: var(--muted); margin-top: 12px; }}
  .source-static {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 10px; background: rgba(148,163,184,0.15); color: var(--muted); }}
  footer {{ padding: 16px 32px; color: var(--muted); font-size: 11px; border-top: 1px solid var(--border); }}
</style>
</head>
<body>
<header>
  <h1>Case Router Lab — Unified Route Matrix</h1>
  <div class="subtitle">5 canonical cases · precheck → triage → capability decision · side-effect free · <span class="source-static">source=static_fallback (canonical demo payloads, computed live)</span></div>
</header>
<main>
  <section class="panel">
    <h2>Route Matrix</h2>
    <table>
      <thead>
        <tr>
          <th>Case ID</th><th>PO ID</th><th>Precheck</th><th>Case Type</th>
          <th>Agent</th><th>Exception Type</th><th>Confidence</th>
          <th>Final Route</th><th>Capability Decision</th>
          <th>Human</th><th>Exec Allowed</th><th>Recommended Workflow</th>
        </tr>
      </thead>
      <tbody>{table_rows}
      </tbody>
    </table>
  </section>

  <section class="panel">
    <h2>How This Router Works</h2>
    <div class="description">
      Rules handle clear normal cases first. Agents handle exceptions and ambiguous cases.
      Low-confidence cases go to manual investigation. Capability evolution only creates
      governed decisions/proposals, <strong>never auto-executes</strong>.
      <br><br>
      <strong>POST /case-intake/route</strong> composes precheck → triage → capability decision
      into a single route plan for downstream work queues and review gates.
    </div>
    <div class="api-hint">
$ curl -X POST http://localhost:8002/case-intake/route \\<br>
&nbsp;&nbsp; -H "Content-Type: application/json" \\<br>
&nbsp;&nbsp; -d @uipath-workflows/http-request-bodies/triage-po-1000.json
    </div>
  </section>
</main>
<footer>Source: live precheck + triage + capability decision</footer>
</body>
</html>"""


@app.get("/case-router-lab", response_class=HTMLResponse)
def case_router_lab() -> HTMLResponse:
    """Render the Case Router Lab page — a full route matrix for 5 cases.

    For each of the 5 canonical cases (PO-1000 ~ PO-1004), the page invokes
    the unified route planner (precheck → triage → capability decision) and
    displays the result in a single matrix. Side-effect free.
    """
    plans = [
        _build_route_plan(TriageRequest(**payload))
        for payload in _ROUTER_LAB_PAYLOADS
    ]
    return HTMLResponse(content=_render_router_lab_html(plans))


# ===========================================================================
# Governance Policy Gate
#
# A pure policy gate that runs AFTER the route plan. It does not execute
# business actions — it only outputs a governance decision: whether UiPath is
# allowed to execute, whether human review is required, whether the case must
# wait, or whether manual investigation is mandatory.
#
# Side-effect free: does not write memory, does not modify XAML, does not
# deploy APIs, does not auto-上线.
# ===========================================================================


class PolicyGateRequest(BaseModel):
    """Input for the governance policy gate."""

    case_id: str
    po_id: str | None = None
    case_type: str = "unknown"
    precheck_result: str = "AMBIGUOUS"
    detected_exception_type: str = "unknown"
    confidence: float | None = None
    final_route: str = "WAITING_MANUAL_INVESTIGATION"
    capability_decision: str = "MANUAL_INVESTIGATION"
    execution_mode: str = "NONE"


def _evaluate_policy_gate(payload: PolicyGateRequest) -> dict[str, Any]:
    """Evaluate the governance policy gate for a route plan.

    Returns a policy decision dict. Pure function — no side effects.
    """
    case_id = payload.case_id
    case_type = payload.case_type
    detected = payload.detected_exception_type
    confidence = payload.confidence
    final_route = payload.final_route
    capability_decision = payload.capability_decision

    # Rule 1: normal + NO_EVOLUTION_REQUIRED -> ALLOW_STANDARD_PROCESSING
    if (
        case_type == "normal"
        and capability_decision == "NO_EVOLUTION_REQUIRED"
    ):
        return {
            "case_id": case_id,
            "policy_decision": "ALLOW_STANDARD_PROCESSING",
            "execution_allowed": True,
            "allowed_execution_mode": "STANDARD",
            "human_required": False,
            "validation_required": False,
            "reason": (
                "Normal purchase order with no capability evolution needed; "
                "standard processing is allowed without additional gates."
            ),
            "required_gates": [],
            "blocked_actions": [],
            "audit_required": False,
        }

    # Rule 5 (checked early): low confidence or MANUAL_INVESTIGATION
    if (
        (confidence is not None and confidence < 0.75)
        or capability_decision == "MANUAL_INVESTIGATION"
    ):
        return {
            "case_id": case_id,
            "policy_decision": "REQUIRE_MANUAL_INVESTIGATION",
            "execution_allowed": False,
            "allowed_execution_mode": "NONE",
            "human_required": True,
            "validation_required": False,
            "reason": (
                "Low confidence or ambiguous business state requires manual "
                "investigation before any execution is permitted."
            ),
            "required_gates": ["MANUAL_INVESTIGATION"],
            "blocked_actions": [
                "AUTO_EXECUTE_WITHOUT_INVESTIGATION",
                "CAPABILITY_REUSE",
                "API_MODERNIZATION",
            ],
            "audit_required": True,
        }

    # Rule 4: XAML_WORKFLOW_PROPOSAL -> REQUIRE_CAPABILITY_REVIEW
    if capability_decision == "XAML_WORKFLOW_PROPOSAL":
        return {
            "case_id": case_id,
            "policy_decision": "REQUIRE_CAPABILITY_REVIEW",
            "execution_allowed": False,
            "allowed_execution_mode": "NONE",
            "human_required": True,
            "validation_required": False,
            "reason": (
                "Capability gap detected; a XAML workflow proposal has been "
                "created but requires human review before any implementation."
            ),
            "required_gates": ["CAPABILITY_REVIEW", "PROPOSAL_APPROVAL"],
            "blocked_actions": [
                "AUTO_EXECUTE_WITHOUT_APPROVAL",
                "AUTO_GENERATE_XAML",
                "AUTO_DEPLOY_WORKFLOW",
            ],
            "audit_required": True,
            "proposal_only": True,
        }

    if detected == "inventory_shortage" or final_route == "CAPABILITY_GAP_DETECTED":
        return {
            "case_id": case_id,
            "policy_decision": "REQUIRE_CAPABILITY_REVIEW",
            "execution_allowed": False,
            "allowed_execution_mode": "NONE",
            "human_required": True,
            "validation_required": False,
            "reason": (
                "Inventory or capability-gap route requires supply-chain review "
                "before ERP execution."
            ),
            "required_gates": ["CAPABILITY_REVIEW", "PROPOSAL_APPROVAL"],
            "blocked_actions": [
                "AUTO_EXECUTE_WITHOUT_APPROVAL",
                "AUTO_GENERATE_XAML",
                "AUTO_DEPLOY_WORKFLOW",
            ],
            "audit_required": True,
            "proposal_only": True,
        }

    # Rule 2: budget_exceeded -> REQUIRE_HUMAN_APPROVAL
    if detected == "budget_exceeded" or final_route == "WAITING_FOR_HUMAN_APPROVAL":
        return {
            "case_id": case_id,
            "policy_decision": "REQUIRE_HUMAN_APPROVAL",
            "execution_allowed": False,
            "allowed_execution_mode": "RPA_OR_API_AFTER_APPROVAL",
            "human_required": True,
            "validation_required": True,
            "reason": (
                "High-risk budget exception requires business approval before "
                "execution."
            ),
            "required_gates": ["BUSINESS_APPROVAL", "VALIDATION_GATE"],
            "blocked_actions": ["AUTO_EXECUTE_WITHOUT_APPROVAL"],
            "audit_required": True,
        }

    # Rule 3: vendor_info_missing -> WAIT_FOR_BUSINESS_DATA
    if detected == "vendor_info_missing" or final_route == "WAITING_VENDOR_INFO":
        return {
            "case_id": case_id,
            "policy_decision": "WAIT_FOR_BUSINESS_DATA",
            "execution_allowed": False,
            "allowed_execution_mode": "NONE",
            "human_required": True,
            "validation_required": False,
            "reason": (
                "Vendor information is missing; execution must wait until "
                "business data is completed."
            ),
            "required_gates": ["BUSINESS_DATA_COMPLETION"],
            "blocked_actions": [
                "AUTO_EXECUTE_WITHOUT_DATA",
                "CAPABILITY_REUSE",
            ],
            "audit_required": True,
        }

    # Default fallback: any proposal-type decision is blocked.
    if "PROPOSAL" in capability_decision:
        return {
            "case_id": case_id,
            "policy_decision": "REQUIRE_CAPABILITY_REVIEW",
            "execution_allowed": False,
            "allowed_execution_mode": "NONE",
            "human_required": True,
            "validation_required": False,
            "reason": (
                f"Capability decision {capability_decision} requires human "
                "review; auto-execution is not allowed for any proposal type."
            ),
            "required_gates": ["CAPABILITY_REVIEW", "PROPOSAL_APPROVAL"],
            "blocked_actions": [
                "AUTO_EXECUTE_WITHOUT_APPROVAL",
                "AUTO_DEPLOY_WORKFLOW",
            ],
            "audit_required": True,
            "proposal_only": True,
        }

    # Final fallback: manual investigation.
    return {
        "case_id": case_id,
        "policy_decision": "REQUIRE_MANUAL_INVESTIGATION",
        "execution_allowed": False,
        "allowed_execution_mode": "NONE",
        "human_required": True,
        "validation_required": False,
        "reason": (
            "Unrecognized route plan; manual investigation required before "
            "execution."
        ),
        "required_gates": ["MANUAL_INVESTIGATION"],
        "blocked_actions": ["AUTO_EXECUTE_WITHOUT_INVESTIGATION"],
        "audit_required": True,
    }


@app.post("/policy-gate/evaluate")
def policy_gate_evaluate(payload: PolicyGateRequest) -> dict[str, Any]:
    """Evaluate the governance policy gate for a route plan.

    Returns a policy decision (ALLOW_STANDARD_PROCESSING /
    REQUIRE_HUMAN_APPROVAL / WAIT_FOR_BUSINESS_DATA /
    REQUIRE_CAPABILITY_REVIEW / REQUIRE_MANUAL_INVESTIGATION).

    Side-effect free: does not write memory, does not modify XAML, does not
    deploy APIs, does not auto-上线.
    """
    return _evaluate_policy_gate(payload)


def _render_policy_lab_html(rows: list[dict[str, Any]]) -> str:
    """Render the Policy Gate Lab page — a policy matrix for 5 cases."""
    import html as html_lib

    def _esc(val: Any) -> str:
        return html_lib.escape(str(val)) if val is not None else ""

    table_rows = ""
    for r in rows:
        case_id = r.get("case_id", "—")
        policy = r.get("policy_decision", "—")
        exec_allowed = "✅ Yes" if r.get("execution_allowed") else "❌ No"
        human_req = "✅ Yes" if r.get("human_required") else "❌ No"
        gates = ", ".join(r.get("required_gates", [])) or "—"
        blocked = ", ".join(r.get("blocked_actions", [])) or "—"

        policy_badge_class = {
            "ALLOW_STANDARD_PROCESSING": "badge-allow",
            "REQUIRE_HUMAN_APPROVAL": "badge-approval",
            "WAIT_FOR_BUSINESS_DATA": "badge-wait",
            "REQUIRE_CAPABILITY_REVIEW": "badge-review",
            "REQUIRE_MANUAL_INVESTIGATION": "badge-manual",
        }.get(policy, "badge-unknown")

        table_rows += f"""
        <tr>
          <td><a href="/case-dashboard/{_esc(case_id)}"><strong>{_esc(case_id)}</strong></a></td>
          <td>{_esc(r.get('final_route', '—'))}</td>
          <td>{_esc(r.get('capability_decision', '—'))}</td>
          <td><span class="badge {policy_badge_class}">{_esc(policy)}</span></td>
          <td>{exec_allowed}</td>
          <td>{human_req}</td>
          <td>{_esc(gates)}</td>
          <td>{_esc(blocked)}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Policy Gate Lab — Governance Decision Matrix</title>
<style>
  :root {{
    --bg: #0f172a; --panel: #1e293b; --panel-2: #273449;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
    --green: #4ade80; --amber: #fbbf24; --red: #f87171; --purple: #c084fc; --blue: #60a5fa;
    --border: #334155;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); font-size: 13px; }}
  header {{ background: linear-gradient(90deg, #1e293b, #0f172a); padding: 18px 32px; border-bottom: 1px solid var(--border); }}
  header h1 {{ margin: 0; font-size: 22px; }}
  header .subtitle {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  main {{ padding: 24px 32px; display: grid; grid-template-columns: 1fr; gap: 20px; }}
  .panel {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 18px; }}
  .panel h2 {{ margin: 0 0 12px 0; font-size: 15px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--accent); }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; padding: 10px 8px; border-bottom: 2px solid var(--border); color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; }}
  td {{ padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  td a {{ color: var(--accent); text-decoration: none; }}
  td a:hover {{ text-decoration: underline; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; white-space: nowrap; }}
  .badge-allow {{ background: rgba(74,222,128,0.18); color: var(--green); }}
  .badge-approval {{ background: rgba(251,191,36,0.18); color: var(--amber); }}
  .badge-wait {{ background: rgba(96,165,250,0.18); color: var(--blue); }}
  .badge-review {{ background: rgba(192,132,252,0.18); color: var(--purple); }}
  .badge-manual {{ background: rgba(248,113,113,0.18); color: var(--red); }}
  .badge-unknown {{ background: rgba(148,163,184,0.18); color: var(--muted); }}
  .description {{ background: var(--panel-2); border-left: 3px solid var(--accent); padding: 14px 18px; border-radius: 6px; color: var(--text); line-height: 1.6; }}
  footer {{ padding: 16px 32px; color: var(--muted); font-size: 11px; border-top: 1px solid var(--border); }}
</style>
</head>
<body>
<header>
  <h1>Policy Gate Lab — Governance Decision Matrix</h1>
  <div class="subtitle">5 canonical cases · route plan → policy gate · auto-execution is always blocked for proposals</div>
</header>
<main>
  <section class="panel">
    <h2>Policy Matrix</h2>
    <table>
      <thead>
        <tr>
          <th>Case ID</th><th>Final Route</th><th>Capability Decision</th>
          <th>Policy Decision</th><th>Exec Allowed</th><th>Human Required</th>
          <th>Required Gates</th><th>Blocked Actions</th>
        </tr>
      </thead>
      <tbody>{table_rows}
      </tbody>
    </table>
  </section>

  <section class="panel">
    <h2>Policy Rules</h2>
    <div class="description">
      <strong>ALLOW_STANDARD_PROCESSING</strong> — normal + NO_EVOLUTION_REQUIRED → execution allowed, no human gate.<br>
      <strong>REQUIRE_HUMAN_APPROVAL</strong> — budget_exceeded → business approval + validation gate required.<br>
      <strong>WAIT_FOR_BUSINESS_DATA</strong> — vendor_info_missing → execution blocked until data is completed.<br>
      <strong>REQUIRE_CAPABILITY_REVIEW</strong> — XAML_WORKFLOW_PROPOSAL → capability review and implementation planning required.<br>
      <strong>REQUIRE_MANUAL_INVESTIGATION</strong> — confidence &lt; 0.75 or MANUAL_INVESTIGATION → no execution, no capability reuse.<br><br>
      All proposal types have <strong>auto_execution_allowed = false</strong>. The policy gate never executes business actions — it only outputs governance decisions.
    </div>
  </section>
</main>
<footer>Source: live policy evaluation</footer>
</body>
</html>"""


@app.get("/policy-gate/lab", response_class=HTMLResponse)
def policy_gate_lab() -> HTMLResponse:
    """Render the Policy Gate Lab page — a governance decision matrix for 5 cases.

    For each of the 5 canonical cases, the page builds a route plan (precheck →
    triage → capability decision), then evaluates the policy gate, and displays
    the policy decision in a single matrix. Side-effect free.
    """
    rows: list[dict[str, Any]] = []
    for payload_dict in _ROUTER_LAB_PAYLOADS:
        plan = _build_route_plan(TriageRequest(**payload_dict))
        gate_request = PolicyGateRequest(
            case_id=plan["case_id"],
            po_id=plan.get("po_id"),
            case_type=plan.get("case_type", "unknown"),
            precheck_result=plan.get("precheck_result", "AMBIGUOUS"),
            detected_exception_type=plan.get("detected_exception_type", "unknown"),
            confidence=plan.get("confidence"),
            final_route=plan.get("final_route", "WAITING_MANUAL_INVESTIGATION"),
            capability_decision=plan.get("capability_decision", "MANUAL_INVESTIGATION"),
            execution_mode=plan.get("execution_mode", "NONE"),
        )
        gate_result = _evaluate_policy_gate(gate_request)
        rows.append({
            "case_id": gate_result["case_id"],
            "final_route": plan.get("final_route", "—"),
            "capability_decision": plan.get("capability_decision", "—"),
            "policy_decision": gate_result["policy_decision"],
            "execution_allowed": gate_result["execution_allowed"],
            "human_required": gate_result["human_required"],
            "required_gates": gate_result["required_gates"],
            "blocked_actions": gate_result["blocked_actions"],
        })
    return HTMLResponse(content=_render_policy_lab_html(rows))


# ===========================================================================
# Demo Evidence Export
#
# Read-only endpoints that produce a comprehensive evidence snapshot of the
# entire demo: 5 cases, route plans, policy gates, capability decisions,
# dashboards, governance, and safety boundaries.
#
# Side-effect free: does not write memory, does not create proposals, does not
# register trusted capabilities, does not modify XAML, does not deploy APIs.
# ===========================================================================




def _build_evidence_snapshot() -> dict[str, Any]:
    """Build a comprehensive evidence snapshot for the 5 canonical demo cases.

    Pure function — no side effects. If a run memory does not exist for a
    case, it is marked "missing" (never raises).
    """
    from memory.run_memory import case_dir, load_run_view

    cases: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    policy_gates: list[dict[str, Any]] = []
    capability_decisions: list[dict[str, Any]] = []
    dashboards: list[dict[str, Any]] = []

    for payload_dict in _ROUTER_LAB_PAYLOADS:
        plan = _build_route_plan(TriageRequest(**payload_dict))
        case_id = plan["case_id"]

        # Check for real run memory.
        cd = case_dir(case_id)
        latest_run_id: str | None = None
        run_memory_status = "missing"
        if cd.exists():
            latest_file = cd / "latest_run_id.txt"
            if latest_file.exists():
                try:
                    latest_run_id = latest_file.read_text().strip()
                except OSError:
                    latest_run_id = None
                if latest_run_id:
                    try:
                        load_run_view(latest_run_id)
                        run_memory_status = "present"
                    except Exception:
                        run_memory_status = "error"
                        latest_run_id = None

        gate = plan.get("policy_gate", {})

        # source marker: real_run_memory when a real run exists, else static_fallback.
        case_source = "real_run_memory" if run_memory_status == "present" else "static_fallback"

        cases.append({
            "case_id": case_id,
            "po_id": plan.get("po_id"),
            "case_type": plan.get("case_type"),
            "precheck_result": plan.get("precheck_result"),
            "detected_exception_type": plan.get("detected_exception_type"),
            "confidence": plan.get("confidence"),
            "latest_run_id": latest_run_id,
            "run_memory_status": run_memory_status,
            "source": case_source,
            "recommended_workflow": plan.get("recommended_uipath_workflow"),
            "dashboard_url": plan.get("dashboard_url"),
            "policy_decision": gate.get("policy_decision"),
        })

        routes.append({
            "case_id": case_id,
            "po_id": plan.get("po_id"),
            "precheck_result": plan.get("precheck_result"),
            "agent_required": plan.get("agent_required"),
            "final_route": plan.get("final_route"),
            "next_stage": plan.get("next_stage"),
            "capability_decision": plan.get("capability_decision"),
            "human_required": plan.get("human_required"),
            "execution_allowed": plan.get("execution_allowed"),
            "recommended_uipath_workflow": plan.get("recommended_uipath_workflow"),
        })

        policy_gates.append({
            "case_id": case_id,
            "policy_decision": gate.get("policy_decision"),
            "execution_allowed": gate.get("execution_allowed"),
            "allowed_execution_mode": gate.get("allowed_execution_mode"),
            "human_required": gate.get("human_required"),
            "validation_required": gate.get("validation_required"),
            "required_gates": gate.get("required_gates"),
            "blocked_actions": gate.get("blocked_actions"),
            "audit_required": gate.get("audit_required"),
            "reason": gate.get("reason"),
        })

        capability_decisions.append({
            "case_id": case_id,
            "capability_decision": plan.get("capability_decision"),
            "business_action": plan.get("detected_exception_type"),
            "api_modernization_required": (
                plan.get("capability_decision") == "API_MODERNIZATION_PROPOSAL"
            ),
            "xaml_improvement_required": (
                plan.get("capability_decision") == "XAML_WORKFLOW_PROPOSAL"
            ),
            "requires_human_review": gate.get("human_required", True),
            "proposal_only": gate.get("proposal_only", False),
        })

        dashboards.append({
            "case_id": case_id,
            "dashboard_url": plan.get("dashboard_url"),
            "latest_run_id": latest_run_id,
            "run_memory_status": run_memory_status,
        })

    # Aggregate source summary: real_run_memory if any case has real run memory,
    # else static_fallback.
    real_case_count = sum(1 for c in cases if c.get("source") == "real_run_memory")
    snapshot_source = "real_run_memory" if real_case_count > 0 else "static_fallback"

    return {
        "project": "Agentic ERP Modernization Layer",
        "source": snapshot_source,
        "source_detail": {
            "real_run_memory_cases": real_case_count,
            "static_fallback_cases": len(cases) - real_case_count,
            "route_plans": "static_fallback (computed live from canonical demo payloads)",
            "policy_gates": "static_fallback (computed live from route plans)",
            "note": (
                "Route plans and policy gates are computed live from the 5 "
                "canonical demo payloads. Case-level source marks whether a "
                "real run memory exists for that case_id."
            ),
        },
        "cases": cases,
        "routes": routes,
        "policy_gates": policy_gates,
        "capability_decisions": capability_decisions,
        "dashboards": dashboards,
        "governance": {
            "human_approval_gate": "enabled for all exception/ambiguous cases",
            "validation_gate": "required for budget_exceeded (CASE-001)",
            "proposal_lifecycle": "PROPOSAL_CREATED -> HUMAN_REVIEW_REQUIRED (no auto-trust)",
            "trusted_capability_registry": "not auto-registered; requires explicit approval",
            "policy_gate_endpoints": [
                "POST /policy-gate/evaluate",
                "GET /policy-gate/lab",
            ],
            "auto_execution_allowed": "only for ALLOW_STANDARD_PROCESSING (CASE-000)",
        },
        "safety_boundaries": {
            "no_auto_xaml_modification": True,
            "no_auto_api_deployment": True,
            "no_automatic_trusted_registration": True,
            "proposal_requires_review": True,
            "windows_xaml_unchanged": True,
            "description": (
                "The system never automatically modifies XAML files, deploys "
                "APIs, registers trusted capabilities, or promotes proposals "
                "to TRUSTED status. All proposals require human review. All "
                "exception/ambiguous cases require human approval before "
                "execution."
            ),
        },
        "recommended_demo_order": [
            "1. GET /case-portfolio — overview of all 5 cases",
            "2. GET /case-router-lab — route matrix (precheck → triage → decision)",
            "3. GET /policy-gate/lab — governance decision matrix",
            "4. POST /case-intake/route (PO-1000) — normal case, ALLOW_STANDARD_PROCESSING",
            "5. POST /case-intake/route (PO-1001) — budget exception, REQUIRE_HUMAN_APPROVAL",
            "6. POST /case-intake/route (PO-1004) — ambiguous case, REQUIRE_MANUAL_INVESTIGATION",
            "7. GET /case-dashboard/CASE-001?run_id=RUN-... — real run memory audit",
            "8. GET /demo/evidence-snapshot — full evidence export (JSON)",
            "9. GET /demo/evidence-markdown — full evidence export (Markdown)",
        ],
        "simulation_summary": _build_simulation_summary(),
        "approvals_summary": _build_approvals_summary(),
    }


@app.get("/demo/evidence-snapshot")
def demo_evidence_snapshot() -> dict[str, Any]:
    """Return a comprehensive JSON evidence snapshot of the entire demo.

    Includes 5 cases, route plans, policy gates, capability decisions,
    dashboards, governance, and safety boundaries. Read-only — does not
    write memory, create proposals, register trusted capabilities, modify
    XAML, or deploy APIs.
    """
    return _build_evidence_snapshot()


def _build_evidence_markdown() -> str:
    """Build a Markdown evidence document for the 5 canonical demo cases.

    Pure function — no side effects.
    """
    snap = _build_evidence_snapshot()

    lines: list[str] = []
    lines.append(f"# {snap['project']} — Demo Evidence")
    lines.append("")
    lines.append("> Auto-generated by `GET /demo/evidence-markdown`. "
                 "This document proves the system is a UiPath-governed case "
                 "routing layer, not a single PO-1001 script.")
    lines.append("")

    # Safety boundaries.
    lines.append("## Safety Boundaries")
    lines.append("")
    sb = snap["safety_boundaries"]
    lines.append(f"- **No auto-XAML modification**: `{sb['no_auto_xaml_modification']}`")
    lines.append(f"- **No auto API deployment**: `{sb['no_auto_api_deployment']}`")
    lines.append(f"- **No automatic trusted registration**: `{sb['no_automatic_trusted_registration']}`")
    lines.append(f"- **Proposal requires review**: `{sb['proposal_requires_review']}`")
    lines.append(f"- **Windows XAML unchanged**: `{sb['windows_xaml_unchanged']}`")
    lines.append("")
    lines.append(f"> {sb['description']}")
    lines.append("")

    # Cases table.
    lines.append("## Cases")
    lines.append("")
    lines.append("| Case ID | PO ID | Case Type | Precheck | Exception | Confidence | Latest Run | Policy Decision |")
    lines.append("|---------|-------|-----------|----------|-----------|------------|------------|-----------------|")
    for c in snap["cases"]:
        conf = f"{c['confidence']:.2f}" if c["confidence"] is not None else "—"
        run = c.get("latest_run_id") or "missing"
        lines.append(
            f"| {c['case_id']} | {c['po_id']} | {c['case_type']} | "
            f"{c['precheck_result']} | {c['detected_exception_type']} | "
            f"{conf} | {run} | {c['policy_decision']} |"
        )
    lines.append("")

    # Route plan table.
    lines.append("## Route Plans")
    lines.append("")
    lines.append("| Case ID | Precheck | Agent | Final Route | Capability Decision | Human | Exec | Workflow |")
    lines.append("|---------|----------|-------|-------------|---------------------|-------|------|----------|")
    for r in snap["routes"]:
        agent = "✅" if r["agent_required"] else "❌"
        human = "✅" if r["human_required"] else "❌"
        exec_ok = "✅" if r["execution_allowed"] else "❌"
        lines.append(
            f"| {r['case_id']} | {r['precheck_result']} | {agent} | "
            f"{r['final_route']} | {r['capability_decision']} | {human} | "
            f"{exec_ok} | `{r['recommended_uipath_workflow']}` |"
        )
    lines.append("")

    # Policy gate table.
    lines.append("## Policy Gates")
    lines.append("")
    lines.append("| Case ID | Policy Decision | Exec Allowed | Human | Validation | Required Gates | Blocked Actions |")
    lines.append("|---------|-----------------|--------------|-------|------------|----------------|-----------------|")
    for g in snap["policy_gates"]:
        exec_ok = "✅" if g["execution_allowed"] else "❌"
        human = "✅" if g["human_required"] else "❌"
        val = "✅" if g["validation_required"] else "❌"
        gates = ", ".join(g["required_gates"]) if g["required_gates"] else "—"
        blocked = ", ".join(g["blocked_actions"]) if g["blocked_actions"] else "—"
        lines.append(
            f"| {g['case_id']} | **{g['policy_decision']}** | {exec_ok} | "
            f"{human} | {val} | {gates} | {blocked} |"
        )
    lines.append("")

    # Governance checklist.
    lines.append("## Governance Checklist")
    lines.append("")
    gov = snap["governance"]
    lines.append(f"- **Human approval gate**: {gov['human_approval_gate']}")
    lines.append(f"- **Validation gate**: {gov['validation_gate']}")
    lines.append(f"- **Proposal lifecycle**: {gov['proposal_lifecycle']}")
    lines.append(f"- **Trusted capability registry**: {gov['trusted_capability_registry']}")
    lines.append(f"- **Auto-execution allowed**: {gov['auto_execution_allowed']}")
    lines.append("")

    # Dashboard links.
    lines.append("## Dashboard Links")
    lines.append("")
    for d in snap["dashboards"]:
        status = d.get("run_memory_status", "missing")
        lines.append(
            f"- [{d['case_id']}]({d['dashboard_url']}) — run memory: `{status}`"
            + (f" ({d['latest_run_id']})" if d.get("latest_run_id") else "")
        )
    lines.append("")
    lines.append("- [Case Portfolio](http://localhost:8002/case-portfolio)")
    lines.append("- [Case Router Lab](http://localhost:8002/case-router-lab)")
    lines.append("- [Policy Gate Lab](http://localhost:8002/policy-gate/lab)")
    lines.append("")

    # Recommended demo order.
    lines.append("## Recommended Demo Order")
    lines.append("")
    for step in snap["recommended_demo_order"]:
        lines.append(f"{step}")
    lines.append("")

    return "\n".join(lines)


@app.get("/demo/evidence-markdown", response_class=PlainTextResponse)
def demo_evidence_markdown() -> PlainTextResponse:
    """Return a Markdown evidence document for the demo.

    Suitable for copying to `docs/evidence/demo-evidence.md`. Read-only —
    does not write memory, create proposals, register trusted capabilities,
    modify XAML, or deploy APIs.
    """
    return PlainTextResponse(
        content=_build_evidence_markdown(),
        media_type="text/markdown",
    )


# ===========================================================================
# Simulation Queue — in-memory case queue for real simulation loops.
# The queue does NOT write memory; UiPath Main writes run memory by calling
# the /memory/runs/* endpoints. The queue only tracks case status.
# ===========================================================================


@app.get("/demo/replay", response_class=HTMLResponse, tags=["Demo"])
def demo_replay() -> HTMLResponse:
    """Read-only interactive replay for the UiPath + Agent demo."""
    steps = [
        ("ERP Work Queue", "UiPath RPA", "Robot opens a purchase order work item",
         "UiPath starts from the ERP work queue, reads PO number, amount, budget, system message, and business remarks.",
         "PO-SIM-006", "Amount 15000 / Budget 10000", "/erp/work-queue"),
        ("Enterprise Context", "Agent Service", "Agent retrieves company context",
         "The route agent uses mocked enterprise context: finance policy, quarterly goals, customer risk, and operations constraints.",
         "Demo Manufacturing Group", "Finance policy + Q4 revenue pressure", "/company-context"),
        ("Agent Decision", "LLM-backed Agent", "Agent chooses the next UiPath step",
         "The agent combines ERP fields, business remarks, and enterprise context to return final_route, policy_gate, and recommended_erp_action.",
         "budget_exceeded -> WAITING_FOR_HUMAN_APPROVAL", "recommended action: CREATE_WEB_APPROVAL_TASK", "/case-router-lab"),
        ("Policy Gate", "Governance", "High-risk actions require human approval",
         "If the order is high-risk, UiPath does not click the ERP approval button. It creates a web approval task instead.",
         "REQUIRE_HUMAN_APPROVAL", "blocked: auto execute without approval", "/policy-gate/lab"),
        ("Human Approval", "Business User", "Approval task carries agent reasoning",
         "The approval inbox shows the PO summary, remarks, company context snapshot, and agent recommendation.",
         "PENDING business approval", "approve/reject with audit trail", "/approvals/inbox"),
        ("Run Memory", "Memory Layer", "Every run becomes durable evidence",
         "UiPath records ERP extraction, route response, policy gate, selected branch, and final result as events and artifacts.",
         "ERP fields + route plan + policy gate", "case dashboard links back to run_id", "/simulation/dashboard"),
        ("Pattern Analysis", "Agent + Memory", "Repeated work becomes modernization intelligence",
         "Similar runs are grouped by business action, exception type, route, policy gate, and side effects.",
         "observed_count vs threshold", "API candidate or XAML workflow candidate", "/simulation/dashboard"),
        ("Proposal Pipeline", "Modernization Governance", "Agents propose API or XAML evolution",
         "Stable purchase approvals can become API proposals. Repeated capability gaps can become UiPath XAML workflow proposals.",
         "API_MODERNIZATION_PROPOSAL / XAML_WORKFLOW_PROPOSAL", "human approval required before Codex handoff", "/proposals/inbox"),
        ("Codex Handoff", "Coding Agent", "Approved proposals can become draft PR work",
         "After human approval, a proposal can produce a Codex prompt. The replay never calls Codex automatically.",
         "Draft PR handoff after approval", "code and XAML remain reproducible evidence", "/proposals/inbox"),
    ]
    step_dicts = [
        {
            "label": label,
            "actor": actor,
            "title": title,
            "summary": summary,
            "primary": primary,
            "secondary": secondary,
            "evidence": evidence,
        }
        for label, actor, title, summary, primary, secondary, evidence in steps
    ]

    c: list[str] = []
    c.append("<p class='legacy-note'>Interactive Replay &middot; <a href='/erp/work-queue'>ERP Work Queue</a> &middot; <a href='/monitoring/live'>Live Monitoring</a> &middot; <a href='/simulation/dashboard'>Pattern Memory Dashboard</a> &middot; <a href='/proposals/inbox'>Proposal Inbox</a></p>")
    c.append("<section class='replay-hero'><div><div class='eyebrow'>Product Replay / Simulation</div><h1>UiPath + Agentic ERP Modernization Replay</h1><p>Clickable replay of the real demo flow: UiPath orchestrates ERP work, agents decide with enterprise context, humans approve high-risk actions, and memory drives API or XAML workflow proposals.</p></div><div class='proof-card'><strong>Evidence model</strong><ul><li><b>Online Replay</b>: clickable product simulation</li><li><b>Demo Video</b>: real UiPath Robot execution proof</li><li><b>GitHub</b>: code, backend services, and XAML reproducibility</li></ul></div></section>")
    c.append("<section class='replay-layout'><div class='timeline-panel'><div class='replay-controls'><button type='button' id='playReplay'>Play Replay</button><button type='button' id='pauseReplay'>Pause</button><button type='button' id='nextStep'>Next Step</button><button type='button' id='resetReplay'>Reset</button></div><div class='step-list'>")
    for i, step in enumerate(step_dicts):
        active = " active" if i == 0 else ""
        c.append(f"<button type='button' class='step-btn{active}' data-step='{i}'><span class='step-index'>{i + 1}</span><span><strong>{html_lib.escape(step['label'])}</strong><em>{html_lib.escape(step['actor'])}</em></span></button>")
    c.append("</div></div><div class='stage-panel'><div class='stage-header'><div><div class='eyebrow' id='stageActor'></div><h2 id='stageTitle'></h2></div><a id='evidenceLink' class='button' href='/erp/work-queue'>Open live evidence</a></div><p id='stageSummary'></p><div class='mock-saas'><div class='mock-sidebar'><div>ERP Queue</div><div>Agent Decision</div><div>Policy Gate</div><div>Run Memory</div><div>Proposal Inbox</div></div><div class='mock-main'><div class='mock-card primary' id='mockPrimary'></div><div class='mock-card' id='mockSecondary'></div><div class='decision-strip'><div><strong>UiPath</strong><span>orchestrates ERP UI and evidence</span></div><div><strong>Agent</strong><span>decides next step with context</span></div><div><strong>Governance</strong><span>human approval for high-risk work</span></div></div></div></div></div></section>")
    c.append("<section class='evidence-grid'>")
    for title, body, href in [
        ("Real UiPath Flow", "Robot opens ERP queue, reads fields, and clicks allowed ERP buttons.", "/erp/work-queue"),
        ("Enterprise Context", "Mock company policy, quarterly goals, customer risk, and operations constraints.", "/company-context"),
        ("Human Approval", "High-risk agent decisions are sent to a web approval inbox.", "/approvals/inbox"),
        ("Pattern Memory", "Run Memory is normalized into repeated process signatures and thresholds.", "/simulation/dashboard"),
        ("Proposal Inbox", "API and XAML workflow evolution are proposal-only and human reviewed.", "/proposals/inbox"),
        ("Evidence Export", "Snapshot and markdown pages support submission and review.", "/demo/evidence-markdown"),
    ]:
        c.append(f"<a class='evidence-card' href='{href}'><strong>{title}</strong><span>{body}</span><em>{href}</em></a>")
    c.append("</section>")

    replay_json = json.dumps(step_dicts)
    extra_script = f"""
<script>
const replaySteps = {replay_json};
let replayIndex = 0;
let replayTimer = null;
function renderReplayStep(i) {{
  replayIndex = (i + replaySteps.length) % replaySteps.length;
  const s = replaySteps[replayIndex];
  document.querySelectorAll('.step-btn').forEach((el, idx) => el.classList.toggle('active', idx === replayIndex));
  document.getElementById('stageActor').textContent = s.actor;
  document.getElementById('stageTitle').textContent = s.title;
  document.getElementById('stageSummary').textContent = s.summary;
  document.getElementById('mockPrimary').textContent = s.primary;
  document.getElementById('mockSecondary').textContent = s.secondary;
  document.getElementById('evidenceLink').setAttribute('href', s.evidence);
}}
function playReplay() {{ if (!replayTimer) replayTimer = setInterval(() => renderReplayStep(replayIndex + 1), 2200); }}
function pauseReplay() {{ if (replayTimer) {{ clearInterval(replayTimer); replayTimer = null; }} }}
document.getElementById('playReplay').addEventListener('click', playReplay);
document.getElementById('pauseReplay').addEventListener('click', pauseReplay);
document.getElementById('nextStep').addEventListener('click', () => renderReplayStep(replayIndex + 1));
document.getElementById('resetReplay').addEventListener('click', () => {{ pauseReplay(); renderReplayStep(0); }});
document.querySelectorAll('.step-btn').forEach((el) => el.addEventListener('click', () => renderReplayStep(Number(el.dataset.step))));
renderReplayStep(0);
</script>
"""
    extra_css = """
.replay-hero { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 14px; align-items: stretch; border: 1px solid #9aa8b8; background: linear-gradient(135deg, #10243d, #1e3d5f); color: #fff; padding: 18px; margin-bottom: 12px; }
.replay-hero h1 { margin: 4px 0 8px; padding: 0; border: 0; background: transparent; color: #fff; font-size: 28px; }
.replay-hero p { margin: 0; max-width: 900px; color: #e5edf7; font-size: 14px; }
.eyebrow { color: #69cef7; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }
.proof-card { border: 1px solid rgba(255,255,255,.22); background: rgba(255,255,255,.08); padding: 14px; }
.replay-layout { display: grid; grid-template-columns: 330px minmax(0, 1fr); gap: 12px; margin-bottom: 12px; }
.timeline-panel, .stage-panel { border: 1px solid #9aa8b8; background: #fff; padding: 12px; }
.replay-controls { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; margin-bottom: 10px; }
.step-list { display: grid; gap: 6px; }
.step-btn { display: grid; grid-template-columns: 28px minmax(0, 1fr); gap: 8px; align-items: center; width: 100%; text-align: left; background: #f7f9fc; }
.step-btn.active { background: linear-gradient(#fff9d8, #e8d181); border-color: #8b6f1f; }
.step-index { display: inline-grid; place-items: center; width: 24px; height: 24px; background: #10243d; color: #fff; font-weight: 700; }
.step-btn em { display: block; color: #4e5d70; font-size: 11px; font-style: normal; margin-top: 2px; }
.stage-header { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; border-bottom: 1px solid #c4ccd8; padding-bottom: 8px; margin-bottom: 10px; }
.stage-header h2 { margin: 2px 0 0; padding: 0; border: 0; background: transparent; font-size: 18px; color: #102a47; }
#stageSummary { color: #37475a; font-size: 13px; max-width: 950px; }
.mock-saas { display: grid; grid-template-columns: 180px minmax(0, 1fr); min-height: 340px; border: 1px solid #9aa8b8; background: #eef2f7; }
.mock-sidebar { background: #182b44; color: #dfe8f5; padding: 12px; display: grid; align-content: start; gap: 8px; font-weight: 700; }
.mock-sidebar div { border-bottom: 1px solid rgba(255,255,255,.16); padding-bottom: 8px; }
.mock-main { padding: 14px; }
.mock-card { border: 1px solid #9aa8b8; background: #fff; padding: 16px; margin-bottom: 10px; font-size: 16px; font-weight: 700; color: #102a47; }
.mock-card.primary { background: linear-gradient(#fff, #eaf7ff); border-left: 5px solid #34bdf2; font-size: 22px; }
.decision-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 18px; }
.decision-strip div { border: 1px solid #c4ccd8; background: #fff; padding: 12px; min-height: 88px; }
.decision-strip span { display: block; color: #4e5d70; margin-top: 6px; }
.evidence-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.evidence-card { border: 1px solid #9aa8b8; background: linear-gradient(#fff, #f0f4f8); padding: 12px; min-height: 120px; text-decoration: none; color: #182433; }
.evidence-card strong, .evidence-card span, .evidence-card em { display: block; }
.evidence-card strong { font-size: 14px; color: #102a47; }
.evidence-card span { margin-top: 8px; color: #37475a; }
.evidence-card em { margin-top: 10px; color: #003f8c; font-style: normal; font-size: 11px; }
@media (max-width: 980px) { .replay-hero, .replay-layout, .mock-saas, .evidence-grid { grid-template-columns: 1fr; } .decision-strip { grid-template-columns: 1fr; } }
"""
    html = _render_legacy_shell(
        active_tab="Simulation",
        title="Interactive Replay",
        breadcrumb="Home &gt; Modernization &gt; Interactive Replay",
        content_html="\n".join(c),
        screen_id="DEMO-REPLAY-101",
        extra_css=extra_css,
        extra_script=extra_script,
    )
    return HTMLResponse(content=html)


class SimulationEnqueueRequest(BaseModel):
    """Input for ``POST /simulation/enqueue``."""
    po_id: str
    case_type: str = "exception"
    amount: int = 0
    budget_limit: int = 10000
    vendor_id: str | None = None
    vendor_info_complete: bool = True
    inventory_available: bool = True
    erp_status: str = "Normal"
    raw_exception_text: str = ""
    business_remarks: str = "Routine office purchase. No exception noted."
    agent_context_policy: str | None = None
    business_action: str | None = None
    demo_purpose: str | None = None




@app.post("/simulation/reset", tags=["Simulation"])
def simulation_reset() -> dict[str, Any]:
    """Reset the simulation queue to the default 10 mixed cases.

    Does NOT write memory, create proposals, or modify XAML. Only resets
    the in-memory queue state.
    """
    _reset_simulation_queue()
    return _simulation_state()


@app.post("/simulation/enqueue", tags=["Simulation"])
def simulation_enqueue(payload: SimulationEnqueueRequest) -> dict[str, Any]:
    """Add a custom case to the simulation queue.

    Does NOT write memory. The case is added with status=pending.
    """
    from memory.run_memory import _utc_iso

    cases = _SIMULATION_QUEUE.setdefault("cases", [])
    idx = len(cases) + 1
    case_id = f"CASE-SIM-{idx:03d}"
    case = {
        "simulation_case_id": f"SIM-{idx:03d}",
        "case_id": case_id,
        "po_id": payload.po_id,
        "case_type": payload.case_type,
        "scenario": "custom",
        "amount": payload.amount,
        "budget_limit": payload.budget_limit,
        "vendor_id": payload.vendor_id,
        "vendor_info_complete": payload.vendor_info_complete,
        "inventory_available": payload.inventory_available,
        "erp_status": payload.erp_status,
        "raw_exception_text": payload.raw_exception_text,
        "business_remarks": payload.business_remarks,
        "agent_context_policy": payload.agent_context_policy,
        "business_action": payload.business_action,
        "demo_purpose": payload.demo_purpose,
        "status": "pending",
        "enqueued_at": _utc_iso(),
        "started_at": None,
        "completed_at": None,
        "run_id": None,
        "result": None,
        "final_route": None,
        "policy_decision": None,
        "memory_commit": None,
        "last_action": None,
    }
    cases.append(case)
    return case


# ---------------------------------------------------------------------------
# Scenario templates for /simulation/inject
# ---------------------------------------------------------------------------

_SIMULATION_SCENARIOS: dict[str, dict[str, Any]] = {
    "normal": {
        "case_type": "normal",
        "amount": 5000,
        "budget_limit": 10000,
        "vendor_id": "V-INJ",
        "vendor_info_complete": True,
        "inventory_available": True,
        "erp_status": "Normal",
        "raw_exception_text": "",
        "business_remarks": "Routine office purchase. No exception noted.",
        "business_action": "standard_purchase_order_processing",
        "demo_purpose": "deterministic_precheck",
    },
    "budget_exceeded": {
        "case_type": "exception",
        "amount": 18000,
        "budget_limit": 10000,
        "vendor_id": "V-INJ",
        "vendor_info_complete": True,
        "inventory_available": True,
        "erp_status": "Exception",
        "raw_exception_text": "Amount exceeds approved budget limit",
        "business_remarks": (
            "Q4 customer delivery is at risk. Finance asks whether this should be "
            "approved due to strategic account impact."
        ),
        "agent_context_policy": "fetch_enterprise_context_before_decision",
        "business_action": "request_purchase_order_approval",
        "demo_purpose": "agent_context_human_approval",
    },
    "agent_context_review": {
        "case_type": "exception",
        "amount": 18000,
        "budget_limit": 10000,
        "vendor_id": "V-203",
        "vendor_info_complete": True,
        "inventory_available": True,
        "erp_status": "Exception",
        "raw_exception_text": "Amount exceeds approved budget limit",
        "business_remarks": (
            "Q4 customer delivery is at risk. Finance asks whether this should be "
            "approved due to strategic account impact."
        ),
        "agent_context_policy": "fetch_enterprise_context_before_decision",
        "business_action": "request_purchase_order_approval",
        "demo_purpose": "single_agent_decision_with_company_context",
    },
    "capex_budget_exception": {
        "case_type": "exception",
        "amount": 24000,
        "budget_limit": 10000,
        "vendor_id": "V-CAPEX",
        "vendor_info_complete": True,
        "inventory_available": True,
        "erp_status": "Exception",
        "raw_exception_text": "Amount exceeds approved budget limit",
        "business_remarks": (
            "Q4 capital equipment delivery is at risk for ACME Retail renewal. "
            "Finance asks whether a CAPEX budget exception should be approved."
        ),
        "agent_context_policy": "fetch_enterprise_context_before_decision",
        "business_action": "request_capex_budget_exception_approval",
        "demo_purpose": "api_modernization_proposal_seed",
    },
    "vendor_info_missing": {
        "case_type": "exception",
        "amount": 6000,
        "budget_limit": 10000,
        "vendor_id": None,
        "vendor_info_complete": False,
        "inventory_available": True,
        "erp_status": "Exception",
        "raw_exception_text": "Vendor information missing",
        "business_remarks": (
            "Buyer left a note: vendor tax profile was requested last week but is "
            "not yet attached."
        ),
        "agent_context_policy": "fetch_enterprise_context_before_decision",
        "business_action": "handle_vendor_info_missing",
        "demo_purpose": "waiting_for_business_data",
    },
    "inventory_shortage": {
        "case_type": "exception",
        "amount": 8500,
        "budget_limit": 10000,
        "vendor_id": "V-INJ",
        "vendor_info_complete": True,
        "inventory_available": False,
        "erp_status": "Exception",
        "raw_exception_text": "Inventory shortage",
        "business_remarks": (
            "Operations note: substitute parts may be available but require supply "
            "chain review."
        ),
        "agent_context_policy": "fetch_enterprise_context_before_decision",
        "business_action": "request_inventory_review",
        "demo_purpose": "xaml_workflow_proposal_seed",
    },
    "ambiguous": {
        "case_type": "ambiguous",
        "amount": 9500,
        "budget_limit": 10000,
        "vendor_id": "V-INJ",
        "vendor_info_complete": True,
        "inventory_available": True,
        "erp_status": "PendingReview",
        "raw_exception_text": "Needs business attention before processing.",
        "business_remarks": (
            "Requester says this order supports a renewal opportunity, but the "
            "business justification is incomplete."
        ),
        "agent_context_policy": "fetch_enterprise_context_before_decision",
        "business_action": "manual_case_review",
        "demo_purpose": "manual_investigation",
    },
}


class SimulationInjectRequest(BaseModel):
    """Input for ``POST /simulation/inject``.

    Either specify a ``scenario`` name (with optional ``count``) to inject
    pre-built cases, or pass a full ``case_payload`` for a custom case.
    """
    scenario: str | None = None
    count: int = 1
    case_payload: dict[str, Any] | None = None


def _do_inject(
    *,
    scenario: str | None = None,
    count: int = 1,
    case_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared inject logic used by both JSON and form endpoints.

    Does NOT write memory, create proposals, or modify XAML. Only adds
    cases to the in-memory queue with status=pending.
    """
    from memory.run_memory import _utc_iso

    cases = _SIMULATION_QUEUE.setdefault("cases", [])
    now = _utc_iso()
    injected: list[dict[str, Any]] = []

    if case_payload:
        idx = len(cases) + 1
        case = {
            "simulation_case_id": f"SIM-{idx:03d}",
            "case_id": f"CASE-SIM-{idx:03d}",
            "po_id": case_payload.get("po_id", f"PO-INJ-{idx:03d}"),
            "case_type": case_payload.get("case_type", "exception"),
            "scenario": case_payload.get("scenario", "custom"),
            "amount": case_payload.get("amount", 0),
            "budget_limit": case_payload.get("budget_limit", 10000),
            "vendor_id": case_payload.get("vendor_id"),
            "vendor_info_complete": case_payload.get("vendor_info_complete", True),
            "inventory_available": case_payload.get("inventory_available", True),
            "erp_status": case_payload.get("erp_status", "Normal"),
            "raw_exception_text": case_payload.get("raw_exception_text", ""),
            "business_remarks": case_payload.get(
                "business_remarks",
                "Routine office purchase. No exception noted.",
            ),
            "agent_context_policy": case_payload.get("agent_context_policy"),
            "business_action": case_payload.get("business_action"),
            "demo_purpose": case_payload.get("demo_purpose"),
            "status": "pending",
            "enqueued_at": now,
            "started_at": None,
            "completed_at": None,
            "run_id": None,
            "result": None,
            "final_route": None,
            "policy_decision": None,
            "memory_commit": None,
            "last_action": None,
        }
        cases.append(case)
        injected.append(case)
    elif scenario:
        template = _SIMULATION_SCENARIOS.get(scenario)
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Unknown scenario: '{scenario}'. "
                    f"Supported: {', '.join(sorted(_SIMULATION_SCENARIOS.keys()))}"
                ),
            )
        n = max(1, min(count, 100))
        for _ in range(n):
            idx = len(cases) + 1
            case = {
                "simulation_case_id": f"SIM-{idx:03d}",
                "case_id": f"CASE-SIM-{idx:03d}",
                "po_id": f"PO-INJ-{idx:03d}",
                "scenario": scenario,
                **dict(template),
                "status": "pending",
                "enqueued_at": now,
                "started_at": None,
                "completed_at": None,
                "run_id": None,
                "result": None,
                "final_route": None,
                "policy_decision": None,
                "memory_commit": None,
                "last_action": None,
            }
            cases.append(case)
            injected.append(case)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide either 'scenario' or 'case_payload'.",
        )

    return {
        "injected_count": len(injected),
        "scenario": scenario,
        "cases": injected,
    }


@app.post("/simulation/inject", tags=["Simulation"])
def simulation_inject(payload: SimulationInjectRequest) -> dict[str, Any]:
    """Inject cases into the simulation queue by scenario type or full payload.

    Supports scenarios: normal, budget_exceeded, vendor_info_missing,
    inventory_shortage, ambiguous.

    Does NOT write memory, create proposals, or modify XAML. Only adds
    cases to the in-memory queue with status=pending.
    """
    return _do_inject(
        scenario=payload.scenario,
        count=payload.count,
        case_payload=payload.case_payload,
    )


@app.post("/simulation/inject-form", tags=["Simulation"])
def simulation_inject_form(
    scenario: str = Form(...),
    count: int = Form(1),
):
    """HTML form endpoint for case injection.

    Accepts form-encoded ``scenario`` and ``count`` fields, reuses the
    same inject logic as ``POST /simulation/inject``, then redirects
    (303) to ``/monitoring/live?injected_scenario=...&injected_count=...``.

    Does NOT write memory, create proposals, or modify XAML.
    """
    from urllib.parse import urlencode

    result = _do_inject(scenario=scenario, count=count)
    params = urlencode({
        "injected_scenario": scenario,
        "injected_count": result["injected_count"],
    })
    return RedirectResponse(
        url=f"/monitoring/live?{params}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/simulation/cases/next", tags=["Simulation"])
def simulation_next_case() -> dict[str, Any]:
    """Return the next pending case and mark it as in_progress.

    Does NOT write memory. The caller (UiPath Main) is responsible for
    calling /memory/runs/* endpoints to write run memory.

    If the queue is empty, returns ``has_case=false`` with HTTP 200 (not 404)
    so the robot can poll without error handling for empty queues.
    """
    from memory.run_memory import _utc_iso

    cases = _SIMULATION_QUEUE.get("cases", [])
    for case in cases:
        if case["status"] == "pending":
            case["status"] = "in_progress"
            case["started_at"] = _utc_iso()
            return {
                "has_case": True,
                "queue_empty": False,
                **case,
            }
    return {
        "has_case": False,
        "queue_empty": True,
        "message": "No pending simulation case.",
    }


@app.get("/simulation/state", tags=["Simulation"])
def simulation_state_endpoint() -> dict[str, Any]:
    """Return the current simulation queue state.

    Each case includes: status, run_id, result, final_route, policy_decision,
    memory_commit, started_at, completed_at.
    """
    return _simulation_state()


class SimulationCompleteRequest(BaseModel):
    """Input for ``POST /simulation/cases/complete``."""
    simulation_case_id: str | None = None
    case_id: str | None = None
    po_id: str | None = None
    run_id: str | None = None
    result: str = "SUCCESS"
    final_route: str | None = None
    policy_decision: str | None = None
    memory_commit: str = "COMPLETED"


# Results / memory_commit values that indicate failure.
_FAILURE_RESULTS = {"FAILED", "ERROR", "TIMEOUT", "ABORTED"}
_FAILURE_COMMITS = {"FAILED", "ERROR", "ABORTED"}


@app.post("/simulation/cases/complete", tags=["Simulation"])
def simulation_complete_case(payload: SimulationCompleteRequest) -> dict[str, Any]:
    """Mark a simulation case as completed (or failed).

    Called by UiPath Main after Run Memory commit to close the simulation
    loop: pending → in_progress → completed/failed.

    Does NOT write memory, create proposals, or modify XAML. Only updates
    the in-memory queue status.

    If ``result`` or ``memory_commit`` explicitly indicates failure, the case
    is marked as ``failed`` instead of ``completed``. ``memory_commit=SKIPPED``
    is not a business failure; it only means the optional memory commit did not
    return a body to UiPath.
    """
    from memory.run_memory import _utc_iso

    cases = _SIMULATION_QUEUE.get("cases", [])

    # Find the case by simulation_case_id, case_id, or po_id.
    target = None
    for c in cases:
        if payload.simulation_case_id and c.get("simulation_case_id") == payload.simulation_case_id:
            target = c
            break
        if payload.case_id and c.get("case_id") == payload.case_id:
            target = c
            break
        if payload.po_id and c.get("po_id") == payload.po_id:
            target = c
            break

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Simulation case not found: simulation_case_id={payload.simulation_case_id}, "
                f"case_id={payload.case_id}, po_id={payload.po_id}"
            ),
        )

    if target["status"] == "pending" and target.get("last_action") and payload.run_id:
        from memory.run_memory import run_dir

        if run_dir(payload.run_id).exists():
            # Compatibility for an already-clicked ERP action that happened
            # before the claim-on-open fix was loaded. Normal pending cases,
            # or payloads without a real Run Memory run, remain blocked.
            target["status"] = "in_progress"
            target["started_at"] = target.get("started_at") or _utc_iso()

    # Only in_progress cases can be completed.
    if target["status"] != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Case {target.get('simulation_case_id')} is in status "
                f"'{target['status']}', cannot complete. Only 'in_progress' "
                f"cases can be completed."
            ),
        )

    # Determine if this is a failure.
    result_upper = str(payload.result).upper()
    commit_upper = str(payload.memory_commit).upper()
    is_failure = result_upper in _FAILURE_RESULTS or commit_upper in _FAILURE_COMMITS

    new_status = "failed" if is_failure else "completed"

    # Update the case (preserve existing values if payload fields are None).
    target["status"] = new_status
    target["run_id"] = payload.run_id or target.get("run_id")
    target["result"] = payload.result
    target["final_route"] = payload.final_route
    target["policy_decision"] = payload.policy_decision
    target["memory_commit"] = payload.memory_commit
    target["completed_at"] = _utc_iso()

    state = _simulation_state()
    return {
        "simulation_case_id": target.get("simulation_case_id"),
        "case_id": target.get("case_id"),
        "po_id": target.get("po_id"),
        "status": new_status,
        "run_id": target.get("run_id"),
        "result": target.get("result"),
        "final_route": target.get("final_route"),
        "policy_decision": target.get("policy_decision"),
        "memory_commit": target.get("memory_commit"),
        "started_at": target.get("started_at"),
        "completed_at": target.get("completed_at"),
        "queue_summary": {
            "pending": state["pending"],
            "in_progress": state["in_progress"],
            "completed": state["completed"],
            "failed": state["failed"],
        },
    }


@app.post("/simulation/cases/{simulation_case_id}/claim", tags=["Simulation"])
def simulation_claim_case(simulation_case_id: str) -> dict[str, Any]:
    """Explicitly claim a simulation case (pending → in_progress).

    This is the structural alternative to relying on
    GET /simulation/cases/next for claiming. RPA or the ERP UI can call
    this to claim a specific case by ID.

    Returns:
        - 200 with claimed=True if the case was pending and is now in_progress.
        - 200 with claimed=False if the case was already in_progress (idempotent).
        - 200 with claimed=False if the case is completed/failed (terminal, not re-claimed).
        - 404 if the simulation_case_id does not exist.
    """
    return claim_simulation_case(simulation_case_id)


# ===========================================================================
# Robot Worker — heartbeat and status for always-on robot monitoring.
# The robot state is in-memory only; it does NOT write Run Memory.
# ===========================================================================

_ROBOT_STATE: dict[str, Any] = {
    "robot_id": None,
    "status": "idle",
    "current_case_id": None,
    "current_run_id": None,
    "message": "",
    "last_heartbeat_at": None,
    "processed_count": 0,
    "failed_count": 0,
    "idle_count": 0,
    "heartbeat_history": [],
}


class RobotHeartbeatRequest(BaseModel):
    """Input for ``POST /robot/heartbeat``."""
    robot_id: str
    status: str = "running"
    current_case_id: str | None = None
    current_run_id: str | None = None
    message: str = ""
    processed_count: int | None = None
    failed_count: int | None = None


@app.post("/robot/heartbeat", tags=["Robot"])
def robot_heartbeat(payload: RobotHeartbeatRequest) -> dict[str, Any]:
    """Record a robot heartbeat.

    Saves robot state in memory only — does NOT write Run Memory, create
    proposals, or modify XAML.
    """
    from memory.run_memory import _utc_iso

    now = _utc_iso()
    prev = _ROBOT_STATE.get("status", "idle")

    _ROBOT_STATE["robot_id"] = payload.robot_id
    _ROBOT_STATE["status"] = payload.status
    _ROBOT_STATE["current_case_id"] = payload.current_case_id
    _ROBOT_STATE["current_run_id"] = payload.current_run_id
    _ROBOT_STATE["message"] = payload.message
    _ROBOT_STATE["last_heartbeat_at"] = now

    # Update counts if provided, otherwise derive from status transitions.
    if payload.processed_count is not None:
        _ROBOT_STATE["processed_count"] = payload.processed_count
    if payload.failed_count is not None:
        _ROBOT_STATE["failed_count"] = payload.failed_count

    # Track idle transitions.
    if payload.status == "idle" and prev != "idle":
        _ROBOT_STATE["idle_count"] = int(_ROBOT_STATE.get("idle_count", 0)) + 1

    # Keep last 50 heartbeats for audit.
    history: list = _ROBOT_STATE.setdefault("heartbeat_history", [])
    history.append({
        "timestamp": now,
        "robot_id": payload.robot_id,
        "status": payload.status,
        "current_case_id": payload.current_case_id,
        "current_run_id": payload.current_run_id,
        "message": payload.message,
    })
    if len(history) > 50:
        _ROBOT_STATE["heartbeat_history"] = history[-50:]

    return {
        "robot_id": payload.robot_id,
        "status": payload.status,
        "last_heartbeat_at": now,
        "processed_count": _ROBOT_STATE.get("processed_count", 0),
        "failed_count": _ROBOT_STATE.get("failed_count", 0),
        "idle_count": _ROBOT_STATE.get("idle_count", 0),
    }


def _get_robot_status() -> dict[str, Any]:
    """Return the current robot status, enriched with queue-derived counts.

    If the robot hasn't sent a heartbeat, processed_count/failed_count are
    derived from the simulation queue's completed/failed counts.
    """
    state = _simulation_state()
    robot = dict(_ROBOT_STATE)
    # If no heartbeat yet, derive counts from queue.
    if robot.get("last_heartbeat_at") is None:
        robot["processed_count"] = state["completed"]
        robot["failed_count"] = state.get("failed", 0)
        robot["idle_count"] = state["pending"]
    return {
        "robot_id": robot.get("robot_id"),
        "status": robot.get("status", "idle"),
        "current_case_id": robot.get("current_case_id"),
        "current_run_id": robot.get("current_run_id"),
        "message": robot.get("message", ""),
        "last_heartbeat_at": robot.get("last_heartbeat_at"),
        "processed_count": robot.get("processed_count", 0),
        "failed_count": robot.get("failed_count", 0),
        "idle_count": robot.get("idle_count", 0),
    }


@app.get("/robot/status", tags=["Robot"])
def robot_status_endpoint() -> dict[str, Any]:
    """Return the current robot status.

    Does NOT write memory. If no heartbeat has been received, counts are
    derived from the simulation queue.
    """
    return _get_robot_status()


# ===========================================================================
# Proposal Inbox — read-only listing + approve-for-codex (no Codex call).
# ===========================================================================


def _list_proposals() -> list[dict[str, Any]]:
    """List all proposals from memory/proposals/ (excluding _sequence.json)."""
    from memory.run_memory import proposals_root, _read_json

    root = proposals_root()
    if not root.exists():
        return []
    proposals = []
    for path in sorted(root.glob("*.json")):
        if path.name == _PROPOSAL_SEQ_FILE:
            continue
        payload = _read_json(path, {})
        if payload:
            proposals.append(payload)
    return proposals


def _load_proposal(proposal_id: str) -> dict[str, Any] | None:
    """Load a single proposal by ID."""
    from memory.run_memory import proposal_path, _read_json

    path = proposal_path(proposal_id)
    if not path.exists():
        return None
    return _read_json(path, {})


def _proposal_view_dict(p: dict[str, Any]) -> dict[str, Any]:
    """Project a proposal into the inbox view while preserving old keys."""
    evidence_run_ids = list(p.get("evidence_run_ids", []) or [])
    latest_run_id = p.get("run_id") or (evidence_run_ids[-1] if evidence_run_ids else None)
    return {
        "proposal_id": p.get("proposal_id"),
        "proposal_type": p.get("proposal_type"),
        "process_signature": p.get("process_signature"),
        "source_pattern": p.get("process_signature"),
        "source_run_ids": evidence_run_ids,
        "latest_run_id": latest_run_id,
        "case_id": p.get("case_id"),
        "status": p.get("status"),
        "codex_session_id": p.get("codex_session_id"),
        "codex_session_url": p.get("codex_session_url"),
        "observed_count": p.get("observed_count"),
        "threshold": p.get("threshold"),
        "recommended_change": p.get("recommended_change"),
        "human_review_required": bool(
            p.get("requires_human_approval", p.get("requires_human_review", True))
        ),
        "coding_agent_allowed": p.get("coding_agent_allowed", "after_approval_only"),
        "auto_execution_allowed": p.get("auto_execution_allowed", False),
        "created_at": p.get("created_at"),
        # Backward-compatible key retained for existing callers.
        "evidence_run_ids": evidence_run_ids,
    }


def _pattern_agent_analysis(pattern: dict[str, Any], threshold: int) -> dict[str, str]:
    """Summarize deterministic pattern reasoning for dashboard display."""
    observed = int(pattern.get("observed_count", 0) or 0)
    exception_type = str(pattern.get("exception_type", "") or "")
    business_action = str(pattern.get("business_action", "") or "")
    validation_pass = int(pattern.get("validation_pass_count", 0) or 0)
    validation_pass_rate = validation_pass / observed if observed else 0.0
    selector_failures = int(pattern.get("selector_failure_count", 0) or 0)

    if observed < threshold:
        summary = (
            f"Still accumulating evidence: {observed}/{threshold} real run(s). "
            "No proposal is created until the persisted Pattern Memory reaches threshold."
        )
        status = "ACCUMULATING_EVIDENCE"
    elif exception_type == "budget_exceeded" and business_action == "request_capex_budget_exception_approval":
        summary = (
            "Threshold reached for repeated CAPEX budget exception approval work. API modernization is a candidate "
            "because the business action is stable and human approval can gate execution."
        )
        status = "API_CANDIDATE"
    elif exception_type == "inventory_shortage":
        summary = (
            "Threshold reached for an uncovered inventory shortage path. XAML workflow proposal is "
            "preferred because this is a capability gap, not an API writeback path."
        )
        status = "XAML_WORKFLOW_CANDIDATE"
    elif exception_type == "vendor_info_missing":
        summary = (
            "No modernization recommended: vendor information is missing, so the process should wait "
            "for business data rather than create API or XAML capability."
        )
        status = "WAITING_BUSINESS_DATA"
    elif selector_failures >= 3:
        summary = (
            "Selector failures are repeated; improve the RPA/XAML selector strategy after human review."
        )
        status = "XAML_IMPROVEMENT_CANDIDATE"
    elif validation_pass_rate < 0.8:
        summary = (
            "Threshold reached but validation evidence is not strong enough for API modernization. "
            "Keep reviewing runs before promoting a capability."
        )
        status = "REVIEW_REQUIRED"
    else:
        summary = (
            "Pattern has enough observations, but deterministic guardrails did not identify a safe "
            "modernization path."
        )
        status = "KEEP_RPA_MODE"

    return {
        "analysis": summary,
        "status": status,
        "reasoning_mode": "deterministic_rule",
        "llm_provider": "not_invoked",
        "llm_call_mode": "not_invoked",
        "llm_invocation_verified": "false",
        "schema_validated": "true",
        "guardrails_applied": "true",
    }


def _pattern_recommended_next_step(pattern: dict[str, Any], threshold: int) -> str:
    analysis = _pattern_agent_analysis(pattern, threshold)
    status = analysis["status"]
    if status == "ACCUMULATING_EVIDENCE":
        return "KEEP_ACCUMULATING_EVIDENCE"
    if status == "API_CANDIDATE":
        return "API_MODERNIZATION_PROPOSAL"
    if status == "XAML_WORKFLOW_CANDIDATE":
        return "XAML_WORKFLOW_PROPOSAL"
    if status == "XAML_IMPROVEMENT_CANDIDATE":
        return "XAML_IMPROVEMENT_PROPOSAL"
    if status == "WAITING_BUSINESS_DATA":
        return "WAIT_FOR_VENDOR_INFO"
    if status == "REVIEW_REQUIRED":
        return "KEEP_RPA_MODE"
    return "NO_MODERNIZATION"


def _collect_pattern_dashboard_data() -> dict[str, Any]:
    """Read-only aggregation for the Pattern Memory dashboard."""
    from memory.run_memory import runs_root, _read_json
    from memory.patterns import list_patterns

    threshold = proposal_threshold()
    proposals = _list_proposals()
    proposals.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    proposals_by_pattern = {
        p.get("process_signature"): p
        for p in proposals
        if p.get("process_signature")
    }

    runs_dir = runs_root()
    latest_runs: list[dict[str, Any]] = []
    real_run_count = 0
    run_state_by_id: dict[str, dict[str, Any]] = {}
    if runs_dir.exists():
        for run_dir in sorted(runs_dir.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            real_run_count += 1
            run_id = run_dir.name
            run_state = _read_json(run_dir / "normalized" / "case_state.json", {}) or {}
            process_sig = _read_json(run_dir / "normalized" / "process_signature.json", {}) or {}
            decision = _read_json(
                run_dir / "evolution" / "capability_evolution_decision.json", {}
            ) or {}
            case_id = run_state.get("case_id") or decision.get("case_id") or ""
            row = {
                "run_id": run_id,
                "case_id": case_id,
                "po_id": run_state.get("po_id", ""),
                "process_signature": process_sig.get("process_signature", ""),
                "final_route": run_state.get("current_stage") or run_state.get("final_stage", ""),
                "branch_result": run_state.get("result", ""),
                "execution_mode": run_state.get("execution_mode", ""),
                "status": run_state.get("status", ""),
                "decision": decision.get("decision", ""),
                "dashboard_url": (
                    f"/case-dashboard/{case_id}?run_id={run_id}"
                    if case_id else f"/memory/runs/{run_id}"
                ),
            }
            run_state_by_id[run_id] = row
            if len(latest_runs) < 10:
                latest_runs.append(row)

    raw_patterns = [
        p for p in list_patterns()
        if p.get("source") == "real_run_memory"
    ]
    raw_patterns.sort(
        key=lambda p: (
            int(p.get("observed_count", 0) or 0),
            str(p.get("updated_at", "")),
        ),
        reverse=True,
    )

    pattern_rows: list[dict[str, Any]] = []
    for p in raw_patterns:
        observed = int(p.get("observed_count", 0) or 0)
        validation_pass = int(p.get("validation_pass_count", 0) or 0)
        validation_rate = validation_pass / observed if observed else 0.0
        latest_ids = list(p.get("latest_run_ids", []) or [])
        latest_run_id = latest_ids[-1] if latest_ids else ""
        proposal = proposals_by_pattern.get(p.get("process_signature"))
        analysis = _pattern_agent_analysis(p, threshold)
        side_effect_stability = p.get("side_effect_stability", 0.0)
        pattern_rows.append({
            "process_signature": p.get("process_signature", ""),
            "business_action": p.get("business_action", ""),
            "exception_type": p.get("exception_type", ""),
            "route_family": p.get("route_family", ""),
            "policy_gate_family": p.get("policy_gate_family", ""),
            "side_effects_family": p.get("side_effects_family", ""),
            "business_remarks_examples": list(p.get("business_remarks_examples", []) or []),
            "company_context_used_examples": list(p.get("company_context_used_examples", []) or []),
            "agent_analysis_examples": list(p.get("agent_analysis_examples", []) or []),
            "observed_count": observed,
            "threshold": threshold,
            "threshold_progress": f"{min(observed, threshold)}/{threshold}",
            "latest_run_id": latest_run_id,
            "latest_run_url": run_state_by_id.get(latest_run_id, {}).get("dashboard_url", ""),
            "validation_pass_rate": validation_rate,
            "side_effects_stability": side_effect_stability,
            "current_decision": analysis["status"],
            "proposal_id": proposal.get("proposal_id") if proposal else "",
            "recommended_next_step": _pattern_recommended_next_step(p, threshold),
            "analysis": analysis,
        })

    open_proposals = [
        p for p in proposals
        if str(p.get("status", "")).upper() not in {"CLOSED", "REJECTED", "TRUSTED"}
    ]
    api_candidates = [
        p for p in raw_patterns
        if _pattern_recommended_next_step(p, threshold) == "API_MODERNIZATION_PROPOSAL"
    ]
    xaml_candidates = [
        p for p in raw_patterns
        if _pattern_recommended_next_step(p, threshold).startswith("XAML_")
    ]

    return {
        "threshold": threshold,
        "real_run_count": real_run_count,
        "pattern_rows": pattern_rows,
        "latest_runs": latest_runs,
        "proposals": proposals,
        "open_proposals": open_proposals,
        "active_patterns": len(raw_patterns),
        "accumulating_patterns": sum(
            1 for p in raw_patterns if int(p.get("observed_count", 0) or 0) < threshold
        ),
        "threshold_patterns": sum(
            1 for p in raw_patterns if int(p.get("observed_count", 0) or 0) >= threshold
        ),
        "api_candidates": len(api_candidates),
        "xaml_candidates": len(xaml_candidates),
    }


@app.get("/proposals/inbox", tags=["Proposals"])
def proposals_inbox(format: str | None = None):
    """List all proposals in the inbox (read-only).

    By default returns a legacy shell HTML page. Use ?format=json for the
    original JSON response.

    Proposals are sorted by created_at descending. Only real proposals
    from memory/proposals/ are listed — no static demo data.
    """
    proposals = _list_proposals()
    # Sort by created_at descending (newest first).
    proposals.sort(key=lambda p: p.get("created_at", ""), reverse=True)

    proposal_dicts = [_proposal_view_dict(p) for p in proposals]

    # JSON format — backward compatibility for API callers.
    if format == "json":
        return {"total": len(proposal_dicts), "proposals": proposal_dicts}

    def _proposal_text(value: Any, fallback: str = "—") -> str:
        if value is None or value == "":
            return fallback
        return html_lib.escape(str(value))

    def _proposal_id_path(value: Any) -> str:
        return urllib.parse.quote(str(value or ""), safe="")

    def _proposal_run_links(values: list[Any]) -> str:
        rendered = []
        for value in values:
            text = _proposal_text(value)
            rendered.append(f"<li><code>{text}</code></li>")
        if not rendered:
            return "<span class='muted'>—</span>"
        return "<ul class='proposal-evidence-list'>" + "".join(rendered) + "</ul>"

    # Default: legacy shell HTML page.
    c: list[str] = []
    c.append("<div class='proposal-inbox-page'>")
    c.append(f"<p class='legacy-note'>{len(proposal_dicts)} proposal(s) &middot; "
             f"<a href='/proposals/inbox?format=json'>View as JSON</a> &middot; "
             f"<a href='/monitoring/live'>Monitoring</a> &middot; "
             f"<a href='/simulation/dashboard'>Simulation Dashboard</a></p>")

    c.append("<div class='legacy-panel'>")
    c.append("<h2>Proposal Inbox</h2>")
    c.append("<div class='legacy-panel-body'>")

    if not proposal_dicts:
        c.append("<p>No real proposals yet.</p>")
        c.append("<p>To create one, run repeated real UiPath cases until a pattern threshold is reached.</p>")
    else:
        c.append("<p class='legacy-note'>Each row is a governed capability proposal. Open Details for source pattern, recommended change, and evidence without stretching the inbox.</p>")
        c.append("<div class='proposal-table-wrap'>")
        c.append("<table class='legacy-grid proposal-table'>")
        c.append("<thead><tr><th>Proposal</th><th>Type</th><th>Evidence</th>"
                 "<th>Review Gate</th><th>Status</th><th>Codex Handoff</th>"
                 "<th>Details</th><th>View</th></tr></thead><tbody>")
        for p in proposal_dicts:
            proposal_id = p.get("proposal_id") or ""
            proposal_path_id = _proposal_id_path(proposal_id)
            latest_run = p.get("latest_run_id") or "—"
            codex_session_url = p.get("codex_session_url")
            if codex_session_url:
                codex_action = (
                    f"<a href='{html_lib.escape(str(codex_session_url), quote=True)}' "
                    "class='button codex-session-btn'>View Codex Session</a>"
                )
            else:
                codex_action = (
                    f"<form method='post' action='/proposals/{proposal_path_id}/approve-and-start-codex-form' "
                    f"class='codex-handoff-form'>"
                    f"<button type='submit' class='btn codex-start-btn'>Approve and Start Codex CLI</button>"
                    f"</form>"
                )
            source_pattern = _proposal_text(p.get("source_pattern"))
            recommended_change = _proposal_text(p.get("recommended_change"))
            evidence_runs = _proposal_run_links(list(p.get("source_run_ids", []) or []))
            human_review = _proposal_text(p.get("human_review_required"))
            coding_agent = _proposal_text(p.get("coding_agent_allowed"))
            auto_execution = _proposal_text(p.get("auto_execution_allowed"))
            c.append(
                f"<tr>"
                f"<td class='proposal-id-cell'><strong>{_proposal_text(proposal_id)}</strong>"
                f"<span class='microline'>{_proposal_text(p.get('case_id'))}</span></td>"
                f"<td>{_proposal_text(p.get('proposal_type'))}</td>"
                f"<td><strong>{_proposal_text(p.get('observed_count'))}/{_proposal_text(p.get('threshold'))}</strong>"
                f"<span class='microline'>latest {_proposal_text(latest_run)}</span></td>"
                f"<td>{'Required' if p.get('human_review_required') else 'Optional'}"
                f"<span class='microline'>agent {coding_agent}</span></td>"
                f"<td class='status-pending'>{_proposal_text(p.get('status'))}</td>"
                f"<td class='proposal-action-cell'>{codex_action}</td>"
                "<td><details class='proposal-detail'>"
                "<summary>Details</summary>"
                "<div class='proposal-detail-grid'>"
                f"<section><h3>Source Pattern</h3><p>{source_pattern}</p></section>"
                f"<section><h3>Recommended Change</h3><p>{recommended_change}</p></section>"
                f"<section><h3>Evidence Run IDs</h3>{evidence_runs}</section>"
                f"<section><h3>Governance</h3><p>Human Review: {human_review}</p>"
                f"<p>Coding Agent: {coding_agent}</p><p>Auto Execution: {auto_execution}</p></section>"
                "</div>"
                "</details></td>"
                f"<td><a href='/proposals/{proposal_path_id}' class='button proposal-view-btn'>view</a></td>"
                f"</tr>"
            )
        c.append("</tbody></table></div>")

    c.append("</div></div>")

    c.append("<div class='legacy-note'>")
    c.append("Proposal Inbox — proposals are generated from observed Run Memory and Pattern Memory "
             "when review thresholds are met. Lifecycle status, evidence, and validation readiness are tracked here.")
    c.append("</div>")
    c.append("</div>")

    extra_css = """
.proposal-inbox-page { max-width: 100%; overflow-x: clip; }
.proposal-inbox-page .legacy-panel-body { min-width: 0; }
.proposal-inbox-page table { table-layout: fixed; }
.proposal-inbox-page th,
.proposal-inbox-page td {
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: normal;
  line-height: 1.35;
}
.proposal-table-wrap {
  width: 100%;
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid #c4ccd8;
}
.proposal-table-wrap table { min-width: 900px; border: 0; }
.proposal-table-wrap th:first-child,
.proposal-table-wrap td:first-child { border-left: 0; }
.proposal-table-wrap th:last-child,
.proposal-table-wrap td:last-child { border-right: 0; }
.proposal-table th,
.proposal-table td { font-size: 12px; vertical-align: top; }
.proposal-table th:nth-child(1) { width: 13%; }
.proposal-table th:nth-child(2) { width: 16%; }
.proposal-table th:nth-child(3) { width: 10%; }
.proposal-table th:nth-child(4) { width: 13%; }
.proposal-table th:nth-child(5) { width: 12%; }
.proposal-table th:nth-child(6) { width: 16%; }
.proposal-table th:nth-child(7) { width: 12%; }
.proposal-table th:nth-child(8) { width: 8%; }
.proposal-id-cell strong { display: block; }
.microline {
  display: block;
  margin-top: 3px;
  color: #4e5d70;
  font-size: 11px;
  font-weight: 400;
}
.muted { color: #6b7785; }
.codex-handoff-form { margin: 0; }
.codex-start-btn {
  width: 100%;
  min-width: 0;
  max-width: 190px;
  white-space: normal;
  background: linear-gradient(#e8f5e9, #b7d7bf);
  border-color: #2e7d32;
  color: #123b1b;
}
.codex-session-btn,
.proposal-view-btn {
  width: 100%;
  max-width: 160px;
  white-space: normal;
}
.proposal-detail summary {
  cursor: pointer;
  color: #0d47a1;
  font-weight: 700;
}
.proposal-detail[open] summary { margin-bottom: 8px; }
.proposal-detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px;
  min-width: 520px;
}
.proposal-detail-grid section {
  border: 1px solid #d7e0eb;
  background: #fbfcfe;
  padding: 7px;
}
.proposal-detail-grid h3 {
  margin: 0 0 5px;
  font-size: 11px;
  color: #304258;
}
.proposal-detail-grid p { margin: 0 0 6px; }
.proposal-evidence-list {
  margin: 0;
  padding-left: 16px;
}
.proposal-evidence-list li + li { margin-top: 4px; }
@media (max-width: 760px) {
  .app-title-row {
    height: auto;
    min-height: 42px;
    flex-wrap: wrap;
    gap: 4px 10px;
    padding: 7px 10px;
  }
  .legacy-tabs {
    max-width: 100%;
    overflow-x: auto;
    padding-bottom: 1px;
  }
  .legacy-tabs a {
    flex: 0 0 auto;
    min-width: 92px;
  }
  .erp-body { flex-direction: column; }
  .module-menu {
    width: 100%;
    flex: 0 0 auto;
    max-height: 132px;
    border-right: 0;
    border-bottom: 1px solid #9aa8b8;
  }
  .erp-content-wrap { width: 100%; }
  .erp-content-panel { padding: 8px; }
  .proposal-inbox-page .legacy-note { overflow-wrap: anywhere; }
  .proposal-table-wrap table { min-width: 760px; }
  .proposal-detail-grid { min-width: 320px; }
}
"""
    html = _render_legacy_shell(
        active_tab="Proposals",
        title="Proposal Inbox",
        breadcrumb="Home &gt; Modernization &gt; Proposal Inbox",
        content_html="\n".join(c),
        screen_id="MODERN-PROP-301",
        extra_css=extra_css,
    )
    return HTMLResponse(content=html)


@app.get("/proposals/{proposal_id}", tags=["Proposals"])
def get_proposal(proposal_id: str) -> dict[str, Any]:
    """Return a single proposal by ID (read-only)."""
    proposal = _load_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal not found: {proposal_id}",
        )
    return proposal


class ApproveForCodexResponse(BaseModel):
    proposal_id: str
    previous_status: str
    new_status: str
    codex_prompt: str
    codex_called: bool = False
    xaml_modified: bool = False
    api_deployed: bool = False
    auto_execution_allowed: bool = False
    message: str


class StartCodexCliResponse(BaseModel):
    proposal_id: str
    previous_status: str
    new_status: str
    codex_session_id: str
    codex_session_url: str
    codex_called: bool
    codex_cli_started: bool
    xaml_modified: bool = False
    api_deployed: bool = False
    trusted_capability_registered: bool = False
    auto_execution_allowed: bool = False
    message: str


@app.post(
    "/proposals/{proposal_id}/approve-for-codex",
    response_model=ApproveForCodexResponse,
    tags=["Proposals"],
)
def approve_for_codex(proposal_id: str) -> ApproveForCodexResponse:
    """Approve a proposal for Codex prompt generation.

    Changes the proposal status to APPROVED_FOR_CODEX_PROMPT and returns
    the codex_prompt. Does NOT call Codex, does NOT modify XAML, does NOT
    deploy APIs, does NOT register trusted capabilities.
    """
    from memory.run_memory import proposal_path, _write_json, _read_json, _utc_iso

    path = proposal_path(proposal_id)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal not found: {proposal_id}",
        )

    proposal = _read_json(path, {})
    previous_status = proposal.get("status", "UNKNOWN")

    # Update status and lifecycle.
    now = _utc_iso()
    proposal["status"] = "APPROVED_FOR_CODEX_PROMPT"
    proposal["updated_at"] = now
    lifecycle = proposal.get("lifecycle", [])
    lifecycle.append({
        "stage": "APPROVED_FOR_CODEX_PROMPT",
        "timestamp": now,
        "actor": "human_reviewer",
        "note": "Proposal approved for Codex prompt generation. Codex is NOT called automatically.",
    })
    proposal["lifecycle"] = lifecycle
    _write_json(path, proposal)

    return ApproveForCodexResponse(
        proposal_id=proposal_id,
        previous_status=previous_status,
        new_status="APPROVED_FOR_CODEX_PROMPT",
        codex_prompt=proposal.get("codex_prompt", ""),
        codex_called=False,
        xaml_modified=False,
        api_deployed=False,
        auto_execution_allowed=False,
        message="Proposal approved for Codex prompt. Codex was NOT called. No XAML modified. No API deployed.",
    )


@app.post(
    "/proposals/{proposal_id}/approve-and-start-codex",
    response_model=StartCodexCliResponse,
    tags=["Proposals"],
)
def approve_and_start_codex(proposal_id: str) -> StartCodexCliResponse:
    """Human-approved Codex CLI handoff.

    This endpoint is intentionally separate from ``approve-for-codex``. It is
    called only by an explicit human click from the proposal page. It starts a
    local Codex CLI session and records visible session logs. It still does not
    auto-deploy APIs, modify Windows XAML, register trusted capabilities, or
    auto-merge a PR.
    """
    from memory.run_memory import proposal_path, _write_json, _read_json, _utc_iso

    path = proposal_path(proposal_id)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal not found: {proposal_id}",
        )

    proposal = _read_json(path, {})
    previous_status = proposal.get("status", "UNKNOWN")
    now = _utc_iso()
    session_id = _next_codex_session_id(proposal_id)
    execution_mode = _codex_cli_execution_mode()
    session = {
        "session_id": session_id,
        "proposal_id": proposal_id,
        "proposal_type": proposal.get("proposal_type"),
        "process_signature": proposal.get("process_signature"),
        "execution_mode": execution_mode,
        "execution_mode_label": _codex_cli_mode_label(execution_mode),
        "status": "QUEUED",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "return_code": None,
        "codex_prompt": proposal.get("codex_prompt", ""),
        "command": "queued",
        "current_activity": "Waiting for Codex worker",
        "activity_summary": "Human approval was recorded. The worker will start the Codex handoff session.",
        "activity_events": [
            {
                "timestamp": now,
                "stage": "approval",
                "title": "Human approval captured",
                "detail": "Reviewer clicked Approve and Start Codex CLI from the Proposal Inbox.",
                "status": "completed",
            }
        ],
        "logs": [
            {
                "timestamp": now,
                "line": "Human approved proposal for Codex CLI handoff. Session queued.",
                "summary": "Human approved proposal for Codex CLI handoff. Session queued.",
            }
        ],
        "draft_pr_created": False,
        "draft_pr_handoff_ready": False,
        "api_deployed": False,
        "xaml_modified": False,
        "trusted_capability_registered": False,
    }
    _write_codex_session(session)

    proposal["status"] = "CODEX_CLI_RUNNING"
    proposal["updated_at"] = now
    proposal["codex_session_id"] = session_id
    proposal["codex_session_url"] = f"/codex/sessions/{session_id}"
    lifecycle = proposal.get("lifecycle", [])
    lifecycle.append({
        "stage": "CODEX_CLI_STARTED_BY_HUMAN",
        "timestamp": now,
        "actor": "human_reviewer",
        "note": (
            "Human approved the proposal and started local Codex CLI. "
            "No API deployment, Windows XAML modification, trusted registration, or auto-merge is automatic."
        ),
    })
    proposal["lifecycle"] = lifecycle
    _write_json(path, proposal)

    worker = threading.Thread(
        target=_run_codex_session_worker,
        args=(session_id,),
        daemon=True,
    )
    worker.start()

    return StartCodexCliResponse(
        proposal_id=proposal_id,
        previous_status=previous_status,
        new_status="CODEX_CLI_RUNNING",
        codex_session_id=session_id,
        codex_session_url=f"/codex/sessions/{session_id}",
        codex_called=True,
        codex_cli_started=True,
        message=(
            "Human approval accepted. Codex CLI session started and can be watched from "
            f"/codex/sessions/{session_id}."
        ),
    )


@app.post("/proposals/{proposal_id}/approve-and-start-codex-form", tags=["Proposals"])
def approve_and_start_codex_form(proposal_id: str):
    """Browser form variant for human-approved Codex CLI handoff."""
    result = approve_and_start_codex(proposal_id)
    return RedirectResponse(
        url=result.codex_session_url,
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.get("/codex/sessions/{session_id}/status", tags=["Proposals"])
def codex_session_status(session_id: str) -> dict[str, Any]:
    """Return Codex CLI session status and logs."""
    session = _read_codex_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Codex session not found: {session_id}",
        )
    enriched = dict(session)
    enriched["display_steps"] = _codex_session_display_steps(session)
    enriched["mode_label"] = _codex_cli_mode_label(str(session.get("execution_mode") or "real"))
    return enriched


@app.get("/codex/sessions/{session_id}", tags=["Proposals"], response_class=HTMLResponse)
def codex_session_page(session_id: str) -> HTMLResponse:
    """Human-visible Codex CLI session monitor."""
    session = _read_codex_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Codex session not found: {session_id}",
        )

    def _esc(value: Any) -> str:
        return html_lib.escape(str(value if value is not None else ""))

    def _bool_label(value: Any) -> str:
        return "Yes" if bool(value) else "No"

    logs = list(session.get("logs", []) or [])
    events = list(session.get("activity_events", []) or [])
    steps = _codex_session_display_steps(session)
    raw_log_rows = "".join(
        "<tr>"
        f"<td>{_esc(item.get('timestamp') or '')}</td>"
        f"<td><strong>{_esc(item.get('summary') or _summarize_codex_line(str(item.get('line') or '')))}</strong>"
        f"<details class='raw-line'><summary>Raw line</summary><code>{_esc(item.get('line') or '')}</code></details></td>"
        "</tr>"
        for item in logs[-60:]
    ) or "<tr><td colspan='2'>No raw CLI output yet.</td></tr>"
    event_rows = "".join(
        "<li class='activity-item'>"
        f"<div class='activity-time'>{_esc(item.get('timestamp') or '')}</div>"
        f"<div class='activity-body'><strong>{_esc(item.get('title') or item.get('stage') or 'Activity')}</strong>"
        f"<p>{_esc(item.get('detail') or '')}</p></div>"
        f"<span class='activity-state state-{_esc(str(item.get('status') or 'running').lower())}'>{_esc(item.get('status') or 'running')}</span>"
        "</li>"
        for item in events[-12:]
    ) or "<li class='activity-item'><div class='activity-body'><strong>Waiting for worker</strong><p>No activity events yet.</p></div></li>"
    step_markup = "".join(
        "<div class='codex-step "
        f"step-{_esc(step['state'])}' data-stage='{_esc(step['stage'])}'>"
        f"<span class='step-dot'></span><div><strong>{_esc(step['title'])}</strong><p>{_esc(step['detail'])}</p></div></div>"
        for step in steps
    )
    proposal_id_raw = str(session.get("proposal_id") or "")
    proposal_id = _esc(proposal_id_raw)
    status_label = _esc(session.get("status") or "UNKNOWN")
    mode_label = _codex_cli_mode_label(str(session.get("execution_mode") or "real"))
    mode_note = (
        "Demo mode is active. The page streams staged Codex-like progress without launching an external process."
        if session.get("execution_mode") == "mock"
        else "Real mode is active. The worker launches local codex exec after the human approval click."
    )
    return_code = _esc(session.get("return_code") if session.get("return_code") is not None else "-")
    content = [
        f"<p class='legacy-note'><a href='/proposals/inbox'>Proposal Inbox</a> &middot; "
        f"<a href='/proposals/{proposal_id}'>Proposal JSON</a> &middot; "
        f"<a href='/codex/sessions/{session_id}/status'>Session JSON</a></p>",
        "<section class='codex-hero'>",
        "<div>",
        "<div class='codex-eyebrow'>Human-approved coding agent handoff</div>",
        "<h2>Codex CLI Handoff Session</h2>",
        f"<p id='activity-summary'>{_esc(session.get('activity_summary') or mode_note)}</p>",
        "</div>",
        "<div class='codex-status-block'>",
        f"<span id='mode-label' class='mode-pill mode-{_esc(session.get('execution_mode') or 'real')}'>{_esc(mode_label)}</span>",
        f"<strong id='session-status'>{status_label}</strong>",
        "</div>",
        "</section>",
        "<div class='codex-kpi-grid'>",
        f"<div class='codex-kpi'><span>Proposal</span><strong>{proposal_id}</strong></div>",
        f"<div class='codex-kpi'><span>Type</span><strong>{_esc(session.get('proposal_type') or '')}</strong></div>",
        f"<div class='codex-kpi'><span>Current Activity</span><strong id='current-activity'>{_esc(session.get('current_activity') or 'Queued')}</strong></div>",
        f"<div class='codex-kpi'><span>Return Code</span><strong id='return-code'>{return_code}</strong></div>",
        "</div>",
        "<div class='legacy-panel'>",
        "<h2>Execution Progress</h2>",
        "<div class='legacy-panel-body'>",
        f"<div id='codex-stepper' class='codex-stepper'>{step_markup}</div>",
        "</div></div>",
        "<div class='codex-two-column'>",
        "<div class='legacy-panel'>",
        "<h2>Readable Activity Stream</h2>",
        "<div class='legacy-panel-body'>",
        f"<ol id='activity-list' class='activity-list'>{event_rows}</ol>",
        "</div></div>",
        "<div class='legacy-panel'>",
        "<h2>Safety Boundary</h2>",
        "<div class='legacy-panel-body'>",
        "<table class='form-table boundary-table'>",
        f"<tr><th>Command</th><td><code id='codex-command'>{_esc(session.get('command') or 'queued')}</code></td></tr>",
        f"<tr><th>API Deployed</th><td id='api-deployed'>{_bool_label(session.get('api_deployed'))}</td></tr>",
        f"<tr><th>Windows XAML Modified</th><td id='xaml-modified'>{_bool_label(session.get('xaml_modified'))}</td></tr>",
        f"<tr><th>Trusted Capability Registered</th><td id='trusted-registered'>{_bool_label(session.get('trusted_capability_registered'))}</td></tr>",
        f"<tr><th>Draft PR Handoff Ready</th><td id='draft-pr-status'>{_bool_label(session.get('draft_pr_handoff_ready'))}</td></tr>",
        f"<tr><th>Real PR Created</th><td id='real-pr-created'>{_bool_label(session.get('draft_pr_created'))}</td></tr>",
        "</table>",
        f"<p class='mode-note'>{_esc(mode_note)}</p>",
        "</div></div>",
        "</div>",
        "<details class='legacy-panel raw-output-panel'>",
        f"<summary>Raw CLI Output <span id='raw-count'>{len(logs)}</span> line(s)</summary>",
        "<div class='legacy-panel-body'>",
        "<table id='codex-log-table' class='legacy-grid'><thead><tr><th>Time</th><th>Summary and raw line</th></tr></thead><tbody>",
        raw_log_rows,
        "</tbody></table>",
        "</div></details>",
        "<p class='legacy-note'>This page is evidence that Codex starts only after human proposal approval. "
        "Any generated changes still require normal review and PR handling.</p>",
    ]
    script = f"""
<script>
(function(){{
  function escapeHtml(s){{ if(s===null||s===undefined){{return '';}} return String(s).replace(/[&<>"']/g,function(ch){{return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch];}}); }}
  function boolLabel(v){{ return v ? 'Yes' : 'No'; }}
  function renderSteps(steps){{
    var html = '';
    for(var i=0; i<steps.length; i++){{ var step = steps[i]; html += '<div class="codex-step step-' + escapeHtml(step.state || 'pending') + '" data-stage="' + escapeHtml(step.stage || '') + '"><span class="step-dot"></span><div><strong>' + escapeHtml(step.title || '') + '</strong><p>' + escapeHtml(step.detail || '') + '</p></div></div>'; }}
    return html;
  }}
  function renderActivities(events){{
    var html = '';
    var start = Math.max(0, events.length - 12);
    for(var i=start; i<events.length; i++){{ var item = events[i]; var state = String(item.status || 'running').toLowerCase(); html += '<li class="activity-item"><div class="activity-time">' + escapeHtml(item.timestamp || '') + '</div><div class="activity-body"><strong>' + escapeHtml(item.title || item.stage || 'Activity') + '</strong><p>' + escapeHtml(item.detail || '') + '</p></div><span class="activity-state state-' + escapeHtml(state) + '">' + escapeHtml(item.status || 'running') + '</span></li>'; }}
    if(!html){{ html = '<li class="activity-item"><div class="activity-body"><strong>Waiting for worker</strong><p>No activity events yet.</p></div></li>'; }}
    return html;
  }}
  function renderRawLogs(logs){{
    var html = '';
    var start = Math.max(0, logs.length - 60);
    for(var i=start; i<logs.length; i++){{ var item = logs[i]; var summary = item.summary || item.line || ''; html += '<tr><td>' + escapeHtml(item.timestamp || '') + '</td><td><strong>' + escapeHtml(summary) + '</strong><details class="raw-line"><summary>Raw line</summary><code>' + escapeHtml(item.line || '') + '</code></details></td></tr>'; }}
    if(!html){{ html = '<tr><td colspan="2">No raw CLI output yet.</td></tr>'; }}
    return html;
  }}
  function refreshCodexSession(){{
    fetch('/codex/sessions/{session_id}/status', {{headers: {{'Accept':'application/json'}}, cache:'no-store'}})
      .then(function(resp){{ if(!resp.ok){{ throw new Error('HTTP '+resp.status); }} return resp.json(); }})
      .then(function(d){{
        var status = document.querySelector('#session-status');
        if(status){{ status.textContent = d.status || 'UNKNOWN'; }}
        var mode = document.querySelector('#mode-label');
        if(mode){{ mode.textContent = d.mode_label || d.execution_mode_label || ''; }}
        var current = document.querySelector('#current-activity');
        if(current){{ current.textContent = d.current_activity || 'Queued'; }}
        var summary = document.querySelector('#activity-summary');
        if(summary){{ summary.textContent = d.activity_summary || ''; }}
        var rc = document.querySelector('#return-code');
        if(rc){{ rc.textContent = d.return_code === null || d.return_code === undefined ? '-' : d.return_code; }}
        var command = document.querySelector('#codex-command');
        if(command){{ command.textContent = d.command || 'queued'; }}
        var api = document.querySelector('#api-deployed');
        if(api){{ api.textContent = boolLabel(d.api_deployed); }}
        var xaml = document.querySelector('#xaml-modified');
        if(xaml){{ xaml.textContent = boolLabel(d.xaml_modified); }}
        var trusted = document.querySelector('#trusted-registered');
        if(trusted){{ trusted.textContent = boolLabel(d.trusted_capability_registered); }}
        var draft = document.querySelector('#draft-pr-status');
        if(draft){{ draft.textContent = boolLabel(d.draft_pr_handoff_ready); }}
        var realPr = document.querySelector('#real-pr-created');
        if(realPr){{ realPr.textContent = boolLabel(d.draft_pr_created); }}
        var stepper = document.querySelector('#codex-stepper');
        if(stepper){{ stepper.innerHTML = renderSteps(d.display_steps || []); }}
        var activities = document.querySelector('#activity-list');
        if(activities){{ activities.innerHTML = renderActivities(d.activity_events || []); }}
        var rawCount = document.querySelector('#raw-count');
        if(rawCount){{ rawCount.textContent = (d.logs || []).length; }}
        var tbody = document.querySelector('#codex-log-table tbody');
        if(tbody){{
          tbody.innerHTML = renderRawLogs(d.logs || []);
        }}
      }})
      .catch(function(){{}});
  }}
  setInterval(refreshCodexSession, 2000);
  refreshCodexSession();
}})();
</script>
"""
    extra_css = """
.codex-hero { display: flex; align-items: stretch; justify-content: space-between; gap: 14px; margin-bottom: 10px; padding: 14px; border: 1px solid #9aa8b8; background: linear-gradient(#f8fbff, #e6edf6); }
.codex-hero h2 { margin: 3px 0 6px; padding: 0; border: 0; background: transparent; font-size: 18px; color: #102a47; }
.codex-eyebrow { color: #56697f; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0; }
.codex-status-block { min-width: 180px; display: grid; gap: 8px; align-content: center; justify-items: end; }
.codex-status-block strong { display: inline-block; padding: 6px 10px; border: 1px solid #7f93aa; background: #fff; color: #102a47; font-size: 14px; }
.mode-pill { display: inline-block; padding: 5px 9px; border-radius: 4px; font-weight: 700; border: 1px solid #9aa8b8; background: #fff; color: #24384f; }
.mode-mock { background: #fff7d6; border-color: #d8b94c; color: #513b00; }
.mode-real { background: #e8f5e9; border-color: #75a878; color: #1b5e20; }
.codex-kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 10px; margin-bottom: 10px; }
.codex-kpi { border: 1px solid #9aa8b8; background: #fff; padding: 10px; min-height: 72px; }
.codex-kpi span { display: block; color: #56697f; font-size: 11px; margin-bottom: 6px; }
.codex-kpi strong { display: block; color: #102a47; font-size: 13px; overflow-wrap: anywhere; }
.codex-stepper { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 8px; }
.codex-step { display: grid; grid-template-columns: 18px minmax(0, 1fr); gap: 8px; align-items: start; min-height: 82px; padding: 9px; border: 1px solid #c4ccd8; background: #f8fafc; }
.codex-step strong { display: block; color: #1c324d; margin-bottom: 4px; }
.codex-step p { margin: 0; color: #56697f; line-height: 1.35; }
.step-dot { width: 12px; height: 12px; margin-top: 2px; border-radius: 50%; border: 2px solid #9aa8b8; background: #fff; }
.step-done { background: #eef7ef; border-color: #75a878; }
.step-done .step-dot { background: #2e7d32; border-color: #2e7d32; }
.step-active { background: #eaf2ff; border-color: #7ca2d8; }
.step-active .step-dot { background: #1565c0; border-color: #1565c0; }
.step-failed { background: #ffebee; border-color: #c62828; }
.step-failed .step-dot { background: #c62828; border-color: #c62828; }
.codex-two-column { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.65fr); gap: 10px; align-items: start; }
.activity-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 7px; }
.activity-item { display: grid; grid-template-columns: 170px minmax(0, 1fr) 86px; gap: 9px; align-items: start; padding: 8px; border: 1px solid #d1d8e2; background: #fbfcfe; }
.activity-time { color: #56697f; font-size: 11px; font-family: Consolas, monospace; }
.activity-body strong { display: block; color: #102a47; margin-bottom: 3px; }
.activity-body p { margin: 0; color: #304258; line-height: 1.4; }
.activity-state { justify-self: end; padding: 2px 7px; border-radius: 4px; border: 1px solid #9aa8b8; background: #fff; font-size: 11px; text-transform: uppercase; }
.state-completed, .state-done, .state-success { border-color: #75a878; background: #e8f5e9; color: #1b5e20; }
.state-running { border-color: #7ca2d8; background: #eaf2ff; color: #0f4c8a; }
.state-failed { border-color: #c62828; background: #ffebee; color: #7a1f1f; }
.boundary-table th { width: 190px; }
.boundary-table code, #codex-log-table code { white-space: pre-wrap; word-break: break-word; font-family: Consolas, monospace; }
.mode-note { margin: 8px 0 0; color: #56697f; }
.raw-output-panel { display: block; margin-bottom: 10px; }
.raw-output-panel > summary { cursor: pointer; padding: 7px 9px; border-bottom: 1px solid #9aa8b8; background: linear-gradient(#f9fbfd, #d7e0eb); color: #18314d; font-weight: 700; }
.raw-line { margin-top: 5px; }
.raw-line summary { cursor: pointer; color: #003f8c; }
#codex-log-table td:first-child { width: 210px; }
@media (max-width: 1100px) { .codex-kpi-grid { grid-template-columns: repeat(2, minmax(160px, 1fr)); } .codex-stepper { grid-template-columns: repeat(2, minmax(160px, 1fr)); } .codex-two-column { grid-template-columns: 1fr; } }
@media (max-width: 700px) { .codex-hero { display: block; } .codex-status-block { justify-items: start; margin-top: 10px; } .codex-kpi-grid, .codex-stepper { grid-template-columns: 1fr; } .activity-item { grid-template-columns: 1fr; } .activity-state { justify-self: start; } }
"""
    html = _render_legacy_shell(
        active_tab="Proposals",
        title="Codex CLI Session",
        breadcrumb=f"Home &gt; Modernization &gt; Codex CLI &gt; {html_lib.escape(session_id)}",
        content_html="\n".join(content),
        screen_id="MODERN-CODEX-401",
        extra_css=extra_css,
        extra_script=script,
    )
    return HTMLResponse(content=html)


# ===========================================================================
# Simulation Dashboard — Pattern Memory / Proposal Pipeline view.
# ===========================================================================


@app.get("/simulation/dashboard", tags=["Simulation"])
def simulation_dashboard() -> HTMLResponse:
    """HTML Pattern Memory dashboard for real run aggregation.

    Read-only — does not write memory, create proposals, or modify XAML.
    """
    state = _simulation_state()
    dashboard = _collect_pattern_dashboard_data()
    cases = list(state.get("cases", []))
    visible_cases = cases[:10]
    latest_runs = dashboard["latest_runs"][:10]
    pattern_rows = dashboard["pattern_rows"]
    open_proposals = [_proposal_view_dict(p) for p in dashboard["open_proposals"][:5]]
    demo_samples = [
        ("CASE-000", "PO-1000", "normal", "standard processing"),
        ("CASE-001", "PO-1001", "budget_exceeded", "API modernization sample"),
        ("CASE-002", "PO-1002", "vendor_info_missing", "wait for business data"),
        ("CASE-003", "PO-1003", "inventory_shortage", "XAML workflow sample"),
        ("CASE-004", "PO-1004", "ambiguous", "manual investigation sample"),
    ]
    demo_seed_sets = [
        ("agent_context_review", "1", "Agent reads /company-context and creates a web approval task."),
        ("capex_budget_exception", str(proposal_threshold()), "Repeated CAPEX budget exceptions accumulate toward API_MODERNIZATION_PROPOSAL."),
        ("inventory_shortage", str(proposal_threshold()), "Repeated inventory shortages accumulate toward XAML_WORKFLOW_PROPOSAL."),
    ]

    def _dash_text(value: Any, fallback: str = "—") -> str:
        if value is None or value == "":
            return fallback
        return html_lib.escape(str(value))

    def _dash_items(values: list[Any], *, as_json: bool = False, limit: int = 3) -> str:
        rendered: list[str] = []
        for value in values[:limit]:
            text = json.dumps(value, ensure_ascii=False) if as_json else str(value)
            rendered.append(f"<li>{html_lib.escape(text)}</li>")
        if not rendered:
            return "<span class='muted'>—</span>"
        return "<ul class='evidence-list'>" + "".join(rendered) + "</ul>"

    c: list[str] = []
    c.append("<div class='simulation-dashboard'>")
    c.append(f"<p class='legacy-note'>Pattern Memory Dashboard &middot; Reset at: <code>{state['reset_at'] or 'never'}</code> &middot; "
             f"<a href='/monitoring/live'>Monitoring</a> &middot; "
             f"<a href='/erp/work-queue'>Legacy ERP Work Queue</a> &middot; "
             f"<a href='/proposals/inbox'>Proposal Inbox</a></p>")

    c.append("<div class='dashboard-title'>")
    c.append("<h1>Agentic ERP Modernization — Pattern Memory Dashboard</h1>")
    c.append("<p>UiPath processes ERP cases through RPA. Each completed run writes Run Memory. "
             "The backend normalizes runs into process signatures, updates Pattern Memory, "
             "and only creates proposals after thresholds are reached.</p>")
    c.append("</div>")

    # KPI cards.
    c.append("<div class='stat-grid'>")
    c.append(f"<div class='stat-card'><div class='stat-value'>{dashboard['real_run_count']}</div><div class='stat-label'>Total Real Runs</div></div>")
    c.append(f"<div class='stat-card'><div class='stat-value'>{dashboard['active_patterns']}</div><div class='stat-label'>Active Process Patterns</div></div>")
    c.append(f"<div class='stat-card'><div class='stat-value'>{dashboard['accumulating_patterns']}</div><div class='stat-label'>Patterns Accumulating Evidence</div></div>")
    c.append(f"<div class='stat-card'><div class='stat-value'>{dashboard['threshold_patterns']}</div><div class='stat-label'>Patterns Reached Threshold</div></div>")
    c.append(f"<div class='stat-card'><div class='stat-value'>{len(dashboard['open_proposals'])}</div><div class='stat-label'>Open Proposals</div></div>")
    c.append(f"<div class='stat-card'><div class='stat-value'>{dashboard['api_candidates']}</div><div class='stat-label'>API Candidates</div></div>")
    c.append(f"<div class='stat-card'><div class='stat-value'>{dashboard['xaml_candidates']}</div><div class='stat-label'>XAML Workflow Candidates</div></div>")
    c.append("</div>")

    c.append("<div class='legacy-panel info-panel'>")
    c.append("<h2>What Counts As The Same Process</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append("<p class='signature-formula'><code>process_signature = business_action + exception_type + route_family + policy_gate_family + side_effects_signature</code></p>")
    c.append("<p class='legacy-note'>The persisted backward-compatible key is still <code>business_action__exception_type</code>; "
             "route, policy, and side-effect dimensions are shown as analysis context and run evidence.</p>")
    c.append("<table class='legacy-grid compact-summary'><thead><tr><th>Observed Pattern</th><th>Modernization Meaning</th></tr></thead><tbody>")
    c.append("<tr><td>request_capex_budget_exception_approval + budget_exceeded</td><td>API modernization proposal candidate after repeated real UiPath runs reach threshold.</td></tr>")
    c.append("<tr><td>request_inventory_review + inventory_shortage</td><td>XAML workflow proposal candidate when repeated capability gaps appear.</td></tr>")
    c.append("<tr><td>vendor_info_missing</td><td>Wait for business data; not a modernization candidate.</td></tr>")
    c.append("</tbody></table>")
    c.append("</div></div>")

    # Pattern Memory table.
    c.append("<div class='legacy-panel'>")
    c.append("<h2>Pattern Memory Table</h2>")
    c.append("<div class='legacy-panel-body'>")
    if pattern_rows:
        c.append("<p class='legacy-note'>Each row is one repeated ERP process pattern. Open Evidence to inspect business remarks, company context, and agent analysis without stretching the whole page.</p>")
        c.append("<div class='simulation-table-wrap'>")
        c.append("<table class='legacy-grid pattern-table'><thead><tr>"
                 "<th>Process Pattern</th><th>Scenario</th><th>Evidence</th>"
                 "<th>Route / Policy</th><th>Quality</th><th>Decision</th>"
                 "<th>Proposal</th><th>Next Step</th><th>Details</th>"
                 "</tr></thead><tbody>")
        for row in pattern_rows:
            sig = row["process_signature"]
            encoded_sig = urllib.parse.quote(sig, safe="")
            latest_run = row.get("latest_run_id") or "—"
            latest_run_html = (
                f"<a href='{html_lib.escape(row['latest_run_url'], quote=True)}'>{_dash_text(latest_run)}</a>"
                if row.get("latest_run_url") else _dash_text(latest_run)
            )
            proposal_id = row.get("proposal_id") or "—"
            proposal_html = (
                f"<a href='/proposals/{urllib.parse.quote(proposal_id, safe='')}'>{_dash_text(proposal_id)}</a>"
                if proposal_id != "—" else proposal_id
            )
            analysis = row["analysis"]
            remarks_examples = _dash_items(list(row.get("business_remarks_examples", []) or []))
            context_examples = _dash_items(
                list(row.get("company_context_used_examples", []) or []),
                as_json=True,
            )
            agent_examples = _dash_items(list(row.get("agent_analysis_examples", []) or []))
            analysis_summary = html_lib.escape(str(analysis.get("analysis", "")))
            analysis_meta = (
                f"reasoning_mode={_dash_text(analysis.get('reasoning_mode'))}; "
                f"llm_provider={_dash_text(analysis.get('llm_provider'))}; "
                f"llm_call_mode={_dash_text(analysis.get('llm_call_mode'))}; "
                f"llm_invocation_verified={_dash_text(analysis.get('llm_invocation_verified'))}; "
                f"schema_validated={_dash_text(analysis.get('schema_validated'))}; "
                f"guardrails_applied={_dash_text(analysis.get('guardrails_applied'))}"
            )
            c.append(
                f"<tr>"
                f"<td class='signature-cell'><a href='/patterns/{encoded_sig}'>{_dash_text(sig)}</a>"
                f"<span class='microline'>{_dash_text(row.get('side_effects_family'))}</span></td>"
                f"<td>{_dash_text(row.get('business_action'))}<span class='microline'>{_dash_text(row.get('exception_type'))}</span></td>"
                f"<td><strong>{_dash_text(row.get('threshold_progress'))}</strong><span class='microline'>latest {latest_run_html}</span></td>"
                f"<td>{_dash_text(row.get('route_family'))}<span class='microline'>{_dash_text(row.get('policy_gate_family'))}</span></td>"
                f"<td>{row['validation_pass_rate']:.0%}<span class='microline'>stability {float(row['side_effects_stability'] or 0.0):.2f}</span></td>"
                f"<td>{_dash_text(row.get('current_decision'))}</td>"
                f"<td>{proposal_html}</td>"
                f"<td><strong>{_dash_text(row.get('recommended_next_step'))}</strong></td>"
                "<td><details class='pattern-evidence'>"
                "<summary>Evidence</summary>"
                "<div class='evidence-grid'>"
                f"<section><h3>Business Remarks Examples</h3>{remarks_examples}</section>"
                f"<section><h3>Company Context Used</h3>{context_examples}</section>"
                f"<section><h3>Why Agent Chose Route</h3>{agent_examples}</section>"
                f"<section><h3>Agent Analysis Summary</h3><p>{analysis_summary}</p><p class='microline'>{analysis_meta}</p></section>"
                "</div>"
                "</details></td>"
                f"</tr>"
            )
        c.append("</tbody></table></div>")
    else:
        c.append("<p>No Pattern Memory yet. Inject demo seed cases from <a href='/monitoring/live'>Live Monitoring</a>, let UiPath process them, and commit Run Memory to create process signatures.</p>")
    c.append("</div></div>")

    # Latest runs.
    c.append("<div class='legacy-panel'>")
    c.append("<h2>Latest Real Runs</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append("<p class='legacy-note'>Latest 10 runs only. Open a run for raw events, artifacts, route plan, policy gate evidence, and pattern update.</p>")
    if latest_runs:
        c.append("<table class='legacy-grid'><thead><tr><th>Run ID</th><th>Case ID</th><th>PO ID</th><th>Process Signature</th><th>Final Route</th><th>Branch Result</th><th>Execution Mode</th><th>Status</th><th>Decision</th><th>Agent Trace</th></tr></thead><tbody>")
        for r in latest_runs:
            c.append(
                f"<tr><td><a href='{r['dashboard_url']}'>{r['run_id']}</a></td>"
                f"<td>{r['case_id']}</td>"
                f"<td>{r['po_id']}</td>"
                f"<td>{r['process_signature']}</td>"
                f"<td>{r['final_route']}</td>"
                f"<td>{r['branch_result']}</td>"
                f"<td>{r['execution_mode']}</td>"
                f"<td>{r['status']}</td>"
                f"<td>{r['decision']}</td>"
                f"<td><a href='/agent-trace/{r['run_id']}'>trace</a></td></tr>"
            )
        c.append("</tbody></table>")
    else:
        c.append("<p>No real runs yet. UiPath worker runs will appear here after /memory/runs/start and /memory/runs/{run_id}/commit.</p>")
    c.append("</div></div>")

    # Proposal pipeline.
    c.append("<div class='legacy-panel'>")
    c.append("<h2>Proposal Pipeline</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append(f"<p><strong>{len(dashboard['proposals'])}</strong> real proposal(s), "
             f"<strong>{len(dashboard['open_proposals'])}</strong> open. "
             "<a href='/proposals/inbox'>Open Capability Proposal Inbox</a></p>")
    if open_proposals:
        c.append("<table class='legacy-grid compact-summary'><thead><tr><th>Proposal ID</th><th>Type</th><th>Source Pattern</th><th>Observed</th><th>Threshold</th><th>Status</th></tr></thead><tbody>")
        for p in open_proposals:
            c.append(
                f"<tr><td><a href='/proposals/{p['proposal_id']}'>{p['proposal_id']}</a></td>"
                f"<td>{p['proposal_type']}</td>"
                f"<td>{p['source_pattern']}</td>"
                f"<td>{p['observed_count']}</td>"
                f"<td>{p['threshold']}</td>"
                f"<td>{p['status']}</td></tr>"
            )
        c.append("</tbody></table>")
    else:
        c.append("<p>No real proposals yet. Proposal creation is not button-driven: inject repeated cases, let UiPath complete runs, and wait for Pattern Memory to reach threshold.</p>")
    c.append("</div></div>")

    # Queue table.
    c.append("<div class='legacy-panel'>")
    c.append("<h2>Simulation Queue</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append("<p class='legacy-note'>Current work items waiting for UiPath Robot.</p>")
    c.append("<table class='legacy-grid'><thead><tr><th>Sim ID</th><th>Case ID</th><th>PO ID</th><th>Type</th><th>Status</th><th>Run ID</th><th>Result</th><th>Final Route</th><th>Dashboard</th></tr></thead><tbody>")
    for case in visible_cases:
        sim_id = case.get("simulation_case_id", "")
        case_id = case.get("case_id", "")
        run_id = case.get("run_id") or ""
        if run_id:
            dash_link = f"<a href='/case-dashboard/{case_id}?run_id={run_id}'>{case_id}</a>"
        else:
            dash_link = f"<a href='/case-dashboard/{case_id}'>{case_id}</a>"
        c.append(
            f"<tr class='sim-{case['status']}'><td>{sim_id}</td><td>{case_id}</td><td>{case['po_id']}</td><td>{case['case_type']}</td>"
            f"<td class='status-{case['status']}'>{case['status']}</td>"
            f"<td>{run_id}</td>"
            f"<td>{case.get('result') or ''}</td>"
            f"<td>{case.get('final_route') or ''}</td>"
            f"<td>{dash_link}</td></tr>"
        )
    c.append("</tbody></table>")
    if len(cases) > len(visible_cases):
        c.append(f"<p class='legacy-note'>Showing first {len(visible_cases)} of {len(cases)} work items.</p>")
    c.append("</div></div>")

    c.append("<div class='legacy-panel'>")
    c.append("<h2>Demo Samples / Seed Scenarios</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append("<p class='legacy-note'>Canonical cases remain available as sample dashboards only; the primary view above is real Pattern Memory aggregation.</p>")
    c.append("<table class='legacy-grid compact-summary'><thead><tr><th>Case</th><th>PO</th><th>Scenario</th><th>Role</th></tr></thead><tbody>")
    for sample_case, po_id, scenario, role in demo_samples:
        c.append(
            f"<tr><td><a href='/case-dashboard/{sample_case}'>{sample_case}</a></td>"
            f"<td>{po_id}</td><td>{scenario}</td><td>{role}</td></tr>"
        )
    c.append("</tbody></table>")
    c.append("<h3 class='subsection-title'>Website Click Demo Seed Sets</h3>")
    c.append("<table class='legacy-grid compact-summary'><thead><tr><th>Scenario</th><th>Inject Count</th><th>What It Demonstrates</th><th>Where To Click</th></tr></thead><tbody>")
    for scenario, count, role in demo_seed_sets:
        c.append(
            f"<tr><td>{scenario}</td><td>{count}</td><td>{role}</td>"
            f"<td><a href='/monitoring/live'>Live Monitoring injection panel</a></td></tr>"
        )
    c.append("</tbody></table>")
    c.append("</div></div>")

    c.append("<div class='legacy-panel'>")
    c.append("<h2>Operational Links</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append("<p>Use these links to continue the demo flow from the Pattern Memory overview into live monitoring, work queue handling, approval review, or raw integration endpoints.</p>")
    c.append("<p class='legacy-note'><a href='/monitoring/live'>Live Operations Monitor</a> &middot; "
             "<a href='/erp/work-queue'>Legacy ERP Work Queue</a> &middot; "
             "<a href='/approvals/inbox'>Human Approval Inbox</a> &middot; "
             "<a href='/approvals/approved-pending-writeback'>Approved Pending ERP Writeback</a> &middot; "
             "<a href='/simulation/state'>Simulation State JSON</a> &middot; "
             "<a href='/robot/status'>Robot Status JSON</a> &middot; "
             "<a href='/simulation/inject'>Injection API</a></p>")
    c.append("</div></div>")
    c.append("</div>")

    extra_css = """
.dashboard-title { border: 1px solid #9aa8b8; background: linear-gradient(#fff, #eef3f8); padding: 10px 12px; margin: 8px 0 10px; }
.dashboard-title h1 { margin: 0 0 6px; font-size: 20px; color: #102a47; letter-spacing: 0; }
.dashboard-title p { margin: 0; color: #37475a; max-width: 980px; }
.subsection-title { margin: 10px 0 6px; font-size: 12px; color: #304258; }
.info-panel code { background: #eef3f8; border: 1px solid #c0ccd8; padding: 1px 4px; }
.simulation-dashboard { max-width: 100%; overflow-x: clip; }
.simulation-dashboard .legacy-panel-body { min-width: 0; }
.simulation-dashboard table { table-layout: fixed; }
.simulation-dashboard th,
.simulation-dashboard td {
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: normal;
  line-height: 1.35;
}
.simulation-table-wrap {
  width: 100%;
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid #c4ccd8;
}
.simulation-table-wrap table { min-width: 1080px; border: 0; }
.simulation-table-wrap th:first-child,
.simulation-table-wrap td:first-child { border-left: 0; }
.simulation-table-wrap th:last-child,
.simulation-table-wrap td:last-child { border-right: 0; }
.signature-formula code {
  display: inline-block;
  max-width: 100%;
  white-space: normal;
  overflow-wrap: anywhere;
}
.pattern-table th,
.pattern-table td { font-size: 12px; vertical-align: top; }
.pattern-table th:nth-child(1) { width: 22%; }
.pattern-table th:nth-child(2) { width: 15%; }
.pattern-table th:nth-child(3) { width: 10%; }
.pattern-table th:nth-child(4) { width: 13%; }
.pattern-table th:nth-child(5) { width: 9%; }
.pattern-table th:nth-child(6) { width: 12%; }
.pattern-table th:nth-child(7) { width: 9%; }
.pattern-table th:nth-child(8) { width: 13%; }
.pattern-table th:nth-child(9) { width: 10%; }
.signature-cell a { font-weight: 700; }
.microline {
  display: block;
  margin-top: 3px;
  color: #4e5d70;
  font-size: 11px;
  font-weight: 400;
}
.muted { color: #6b7785; }
.pattern-evidence summary {
  cursor: pointer;
  color: #0d47a1;
  font-weight: 700;
}
.pattern-evidence[open] summary { margin-bottom: 8px; }
.evidence-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px;
  min-width: 520px;
}
.evidence-grid section {
  border: 1px solid #d7e0eb;
  background: #fbfcfe;
  padding: 7px;
}
.evidence-grid h3 {
  margin: 0 0 5px;
  font-size: 11px;
  color: #304258;
}
.evidence-grid p { margin: 0 0 6px; }
.evidence-list {
  margin: 0;
  padding-left: 16px;
}
.evidence-list li + li { margin-top: 4px; }
@media (max-width: 980px) {
  .dashboard-title h1 { font-size: 18px; }
  .simulation-table-wrap table { min-width: 920px; }
  .evidence-grid { min-width: 420px; }
}
@media (max-width: 760px) {
  .app-title-row {
    height: auto;
    min-height: 42px;
    flex-wrap: wrap;
    gap: 4px 10px;
    padding: 7px 10px;
  }
  .legacy-tabs {
    max-width: 100%;
    overflow-x: auto;
    padding-bottom: 1px;
  }
  .legacy-tabs a {
    flex: 0 0 auto;
    min-width: 92px;
  }
  .erp-body { flex-direction: column; }
  .module-menu {
    width: 100%;
    flex: 0 0 auto;
    max-height: 132px;
    border-right: 0;
    border-bottom: 1px solid #9aa8b8;
  }
  .erp-content-wrap { width: 100%; }
  .erp-content-panel { padding: 8px; }
  .simulation-dashboard .legacy-note { overflow-wrap: anywhere; }
  .simulation-table-wrap table { min-width: 760px; }
  .evidence-grid { min-width: 320px; }
}
"""

    html = _render_legacy_shell(
        active_tab="Simulation",
        title="Simulation Dashboard",
        breadcrumb="Home &gt; Simulation &gt; Dashboard",
        content_html="\n".join(c),
        screen_id="SIM-DASH-101",
        extra_css=extra_css,
    )
    return HTMLResponse(content=html)


@app.get("/patterns/{process_signature}", tags=["Patterns"])
def pattern_detail(process_signature: str) -> HTMLResponse:
    """Read-only Pattern Memory detail page.

    Does not write Run Memory, create proposals, call Codex, deploy APIs, or
    register trusted capabilities.
    """
    from memory.patterns import get_pattern

    decoded_signature = urllib.parse.unquote(process_signature)
    pattern = get_pattern(decoded_signature)
    if not pattern or pattern.get("source") != "real_run_memory":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pattern not found: {decoded_signature}",
        )

    dashboard = _collect_pattern_dashboard_data()
    threshold = dashboard["threshold"]
    row = next(
        (
            item for item in dashboard["pattern_rows"]
            if item.get("process_signature") == decoded_signature
        ),
        None,
    )
    analysis = row.get("analysis") if row else _pattern_agent_analysis(pattern, threshold)
    latest_run_ids = list(pattern.get("latest_run_ids", []) or [])
    related_runs = [
        run for run in dashboard["latest_runs"]
        if run.get("run_id") in latest_run_ids
    ]
    proposal = next(
        (
            _proposal_view_dict(p) for p in dashboard["proposals"]
            if p.get("process_signature") == decoded_signature
        ),
        None,
    )

    def _fmt_rate(numerator: Any, denominator: Any) -> str:
        try:
            denom = int(denominator or 0)
            if denom <= 0:
                return "0%"
            return f"{(int(numerator or 0) / denom):.0%}"
        except (TypeError, ValueError):
            return "0%"

    observed = int(pattern.get("observed_count", 0) or 0)
    validation_rate = _fmt_rate(pattern.get("validation_pass_count", 0), observed)
    encoded_signature = urllib.parse.quote(decoded_signature, safe="")

    c: list[str] = []
    c.append("<p class='legacy-note'><a href='/simulation/dashboard'>Pattern Memory Dashboard</a> &middot; "
             "<a href='/proposals/inbox'>Proposal Inbox</a> &middot; "
             "<a href='/monitoring/live'>Monitoring</a></p>")
    c.append("<div class='legacy-panel'>")
    c.append("<h2>Pattern Detail</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append("<table class='legacy-grid'>")
    c.append(f"<tr><td>process_signature</td><td><code>{decoded_signature}</code></td></tr>")
    c.append(f"<tr><td>business_action</td><td>{pattern.get('business_action', '')}</td></tr>")
    c.append(f"<tr><td>exception_type</td><td>{pattern.get('exception_type', '')}</td></tr>")
    c.append(f"<tr><td>observed_count</td><td>{observed}</td></tr>")
    c.append(f"<tr><td>threshold</td><td>{threshold}</td></tr>")
    c.append(f"<tr><td>validation_pass_rate</td><td>{validation_rate}</td></tr>")
    c.append(f"<tr><td>side_effects_stability</td><td>{float(pattern.get('side_effect_stability', 0.0) or 0.0):.2f}</td></tr>")
    c.append(f"<tr><td>current_recommendation</td><td>{pattern.get('current_recommendation', '')}</td></tr>")
    c.append(f"<tr><td>recommended_next_step</td><td>{_pattern_recommended_next_step(pattern, threshold)}</td></tr>")
    c.append(f"<tr><td>proposal_id</td><td>{proposal.get('proposal_id') if proposal else '—'}</td></tr>")
    c.append("</table>")
    c.append("</div></div>")

    c.append("<div class='legacy-panel'>")
    c.append("<h2>Agent Analysis Summary</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append(f"<p>{analysis['analysis']}</p>")
    c.append("<table class='legacy-grid compact-summary'><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>")
    for key in (
        "reasoning_mode",
        "llm_provider",
        "llm_call_mode",
        "llm_invocation_verified",
        "schema_validated",
        "guardrails_applied",
    ):
        c.append(f"<tr><td>{key}</td><td>{analysis.get(key, '')}</td></tr>")
    c.append("</tbody></table>")
    c.append("</div></div>")

    c.append("<div class='legacy-panel'>")
    c.append("<h2>Latest Evidence Runs</h2>")
    c.append("<div class='legacy-panel-body'>")
    if latest_run_ids:
        c.append("<table class='legacy-grid'><thead><tr><th>Run ID</th><th>Case ID</th><th>PO ID</th><th>Status</th><th>Decision</th><th>Dashboard</th></tr></thead><tbody>")
        for run_id in reversed(latest_run_ids[-10:]):
            run = next((item for item in related_runs if item.get("run_id") == run_id), {})
            case_id = run.get("case_id", "")
            dashboard_url = run.get("dashboard_url") or (f"/memory/runs/{run_id}")
            c.append(
                f"<tr><td>{run_id}</td>"
                f"<td>{case_id}</td>"
                f"<td>{run.get('po_id', '')}</td>"
                f"<td>{run.get('status', '')}</td>"
                f"<td>{run.get('decision', '')}</td>"
                f"<td><a href='{dashboard_url}'>open run</a></td></tr>"
            )
        c.append("</tbody></table>")
    else:
        c.append("<p>No latest_run_ids recorded for this pattern.</p>")
    c.append("</div></div>")

    c.append("<div class='legacy-panel'>")
    c.append("<h2>Proposal Pipeline</h2>")
    c.append("<div class='legacy-panel-body'>")
    if proposal:
        c.append("<table class='legacy-grid'>")
        c.append(f"<tr><td>proposal_id</td><td><a href='/proposals/{proposal['proposal_id']}'>{proposal['proposal_id']}</a></td></tr>")
        c.append(f"<tr><td>proposal_type</td><td>{proposal['proposal_type']}</td></tr>")
        c.append(f"<tr><td>observed_count</td><td>{proposal['observed_count']}</td></tr>")
        c.append(f"<tr><td>threshold</td><td>{proposal['threshold']}</td></tr>")
        c.append(f"<tr><td>human_review_required</td><td>{proposal['human_review_required']}</td></tr>")
        c.append(f"<tr><td>coding_agent_allowed</td><td>{proposal['coding_agent_allowed']}</td></tr>")
        c.append(f"<tr><td>auto_execution_allowed</td><td>{proposal['auto_execution_allowed']}</td></tr>")
        c.append("</table>")
    else:
        c.append("<p>No proposal has been created for this pattern yet. Proposals are created only when real Run Memory reaches threshold and evaluator guardrails choose a proposal decision.</p>")
    c.append("</div></div>")

    c.append("<div class='legacy-panel'>")
    c.append("<h2>Business Context Evidence</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append("<table class='legacy-grid'><thead><tr><th>Evidence Type</th><th>Examples</th></tr></thead><tbody>")
    remarks_examples = "<br>".join(
        html_lib.escape(str(v))
        for v in list(pattern.get("business_remarks_examples", []) or [])[:5]
    ) or "—"
    context_examples = "<br>".join(
        html_lib.escape(json.dumps(v, ensure_ascii=False))
        for v in list(pattern.get("company_context_used_examples", []) or [])[:5]
    ) or "—"
    agent_examples = "<br>".join(
        html_lib.escape(str(v))
        for v in list(pattern.get("agent_analysis_examples", []) or [])[:5]
    ) or "—"
    c.append(f"<tr><td>Business Remarks Examples</td><td>{remarks_examples}</td></tr>")
    c.append(f"<tr><td>Company Context Used</td><td>{context_examples}</td></tr>")
    c.append(f"<tr><td>Why Agent Chose The Route</td><td>{agent_examples}</td></tr>")
    c.append("</tbody></table>")
    c.append("</div></div>")

    c.append("<div class='legacy-panel'>")
    c.append("<h2>Pattern Memory JSON</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append(f"<p class='legacy-note'>Read-only source: <code>memory/patterns/{encoded_signature}.json</code></p>")
    c.append("<pre class='pattern-json'>" + html_lib.escape(json.dumps(pattern, indent=2, ensure_ascii=False, default=str)) + "</pre>")
    c.append("</div></div>")

    extra_css = """
.compact-summary th, .compact-summary td { white-space: nowrap; }
.pattern-json { background:#f8fbfe; border:1px solid #c0ccd8; padding:8px; max-height:360px; overflow:auto; font-size:12px; }
"""

    html = _render_legacy_shell(
        active_tab="Simulation",
        title="Pattern Detail",
        breadcrumb="Home &gt; Simulation &gt; Pattern Detail",
        content_html="\n".join(c),
        screen_id="SIM-PATTERN-101",
        extra_css=extra_css,
    )
    return HTMLResponse(content=html)


# ===========================================================================
# Live Monitoring — unified HTML page for real-time robot + queue monitoring.
# ===========================================================================


def _collect_monitoring_live_data() -> dict[str, Any]:
    """Collect all data needed by ``/monitoring/live`` and ``/monitoring/live-data``.

    Pure read-only helper — does NOT write Run Memory, create proposals,
    modify queue state, call Codex, deploy APIs, or modify XAML.

    Returns a JSON-serializable dict with stable field names so the HTML page
    can render it and the JSON endpoint can return it directly.
    """
    from memory.run_memory import runs_root, _read_json, _utc_iso
    from memory.patterns import list_patterns

    robot = _get_robot_status()
    state = _simulation_state()
    proposals = _list_proposals()
    patterns = list_patterns()
    threshold = proposal_threshold()
    pattern_progress: list[dict[str, Any]] = []
    for pattern in patterns:
        if pattern.get("source") != "real_run_memory":
            continue
        observed = int(pattern.get("observed_count", 0) or 0)
        pattern_progress.append({
            "process_signature": pattern.get("process_signature", ""),
            "business_action": pattern.get("business_action", ""),
            "exception_type": pattern.get("exception_type", ""),
            "observed_count": observed,
            "threshold": threshold,
            "progress_label": f"{min(observed, threshold)}/{threshold}",
            "recommended_next_step": _pattern_recommended_next_step(pattern, threshold),
            "latest_run_id": (list(pattern.get("latest_run_ids", []) or [])[-1:] or [""])[0],
        })
    pattern_progress.sort(
        key=lambda p: (int(p.get("observed_count", 0) or 0), str(p.get("process_signature", ""))),
        reverse=True,
    )

    # Collect latest runs (latest 15).
    latest_runs: list[dict[str, Any]] = []
    runs_dir = runs_root()
    if runs_dir.exists():
        for run_dir in sorted(runs_dir.iterdir(), reverse=True):
            if run_dir.is_dir() and len(latest_runs) < 15:
                state_file = run_dir / "normalized" / "case_state.json"
                run_state = _read_json(state_file, {})
                latest_runs.append({
                    "run_id": run_dir.name,
                    "case_id": run_state.get("case_id", ""),
                    "status": run_state.get("status", ""),
                    "execution_mode": run_state.get("execution_mode", ""),
                    "current_stage": run_state.get("current_stage", ""),
                })

    # Build the cases list from simulation queue (latest 15). Monitoring is an
    # event view, so newest injected/updated cases should stay above the fold.
    latest_cases: list[dict[str, Any]] = []
    for c in reversed(state["cases"]):
        if len(latest_cases) >= 15:
            break
        case_id = c.get("case_id", "")
        run_id = c.get("run_id") or ""
        if run_id:
            dashboard_url = f"/case-dashboard/{case_id}?run_id={run_id}"
        else:
            dashboard_url = f"/case-dashboard/{case_id}"
        latest_cases.append({
            "sim_id": c.get("simulation_case_id", ""),
            "case_id": case_id,
            "po_id": c.get("po_id", ""),
            "scenario": c.get("scenario", ""),
            "business_action": c.get("business_action", ""),
            "demo_purpose": c.get("demo_purpose", ""),
            "status": c["status"],
            "run_id": run_id,
            "result": c.get("result") or "",
            "final_route": c.get("final_route") or "",
            "policy_decision": c.get("policy_decision") or "",
            "dashboard_url": dashboard_url,
        })

    # Audit log: classify cases by route type for audit summary.
    audit_counts: dict[str, int] = {
        "normal": 0,
        "exception": 0,
        "waiting": 0,
        "manual": 0,
        "capability_gap": 0,
    }
    for c in state["cases"]:
        route = (c.get("final_route") or "").upper()
        if c.get("case_type") == "normal":
            audit_counts["normal"] += 1
        elif "MANUAL" in route:
            audit_counts["manual"] += 1
        elif "WAITING" in route:
            audit_counts["waiting"] += 1
        elif "CAPABILITY" in route:
            audit_counts["capability_gap"] += 1
        else:
            audit_counts["exception"] += 1

    # Robot heartbeat history (last 10, most-recent first).
    heartbeat_history = list(reversed(_ROBOT_STATE.get("heartbeat_history", [])[-10:]))

    # Approvals summary.
    approvals_summary = _build_approvals_summary()

    return {
        "robot_status": robot,
        "queue_summary": {
            "pending": state["pending"],
            "in_progress": state["in_progress"],
            "completed": state["completed"],
            "failed": state.get("failed", 0),
            "total": state["total"],
        },
        "latest_cases": latest_cases,
        "latest_runs": latest_runs,
        "pattern_counts": patterns,
        "pattern_progress": pattern_progress[:8],
        "proposal_inbox": proposals,
        "audit_log": {
            "counts": audit_counts,
        },
        "heartbeat_history": heartbeat_history,
        "approvals_summary": approvals_summary,
        "server_time": _utc_iso(),
    }


@app.get("/monitoring/live-data", tags=["Monitoring"])
def monitoring_live_data() -> dict[str, Any]:
    """Return the structured JSON data backing ``/monitoring/live``.

    Read-only:
    - Does NOT write Run Memory
    - Does NOT create proposals
    - Does NOT modify queue state (pending/in_progress/completed counts)
    - Does NOT call Codex, deploy APIs, or modify XAML

    Intended for client-side polling (e.g. ``fetch('/monitoring/live-data')``)
    so the monitoring page can refresh individual panels without a full reload.
    """
    return _collect_monitoring_live_data()


@app.get("/monitoring/live", tags=["Monitoring"])
def monitoring_live(
    injected_scenario: str | None = None,
    injected_count: int | None = None,
) -> HTMLResponse:
    """Live monitoring HTML page showing real robot + queue + run memory state.

    Displays:
    - Robot Status: heartbeat, current case, current run, processed/idle counts
    - Queue Summary: pending/in_progress/completed/failed
    - Manual case injection panel
    - Latest Events / audit summary / heartbeat history
    - Human Approval Inbox summary

    Page-rendering only — does not write memory, create proposals, or modify XAML.
    The page uses client-side polling (``fetch('/monitoring/live-data')``) every
    3s instead of a full meta refresh.
    """
    data = _collect_monitoring_live_data()
    robot = data["robot_status"]
    queue = data["queue_summary"]
    latest_cases = data["latest_cases"]
    latest_runs = data["latest_runs"]
    patterns = data["pattern_counts"]
    pattern_progress = data.get("pattern_progress", [])
    proposals = data["proposal_inbox"]
    audit_counts = data["audit_log"]["counts"]
    heartbeat_history = data["heartbeat_history"]
    approvals_summary = data["approvals_summary"]
    pending_approvals = approvals_summary.get("pending", 0)
    total_approvals = approvals_summary.get("total", 0)

    # Build content HTML (panels only; the legacy shell provides head/style/top nav/menu/footer).
    h: list[str] = []
    h.append("<p class='refresh-note'><span id='polling-indicator'>Live data polling: ON</span> · <span id='last-updated'>Last updated: —</span> · Operational status view</p>")
    h.append("<p id='refresh-error' class='refresh-note' style='color:#7a1f1f; display:none;'></p>")

    # --- Robot Status ---
    h.append("<div id='robot-status-panel'>")
    h.append("<h2>Robot Status</h2>")
    h.append("<div class='stat-grid'>")
    h.append(f"<div class='stat-card'><div class='stat-value status-{robot.get('status','idle')}'>{robot.get('status','idle')}</div><div class='stat-label'>Status</div></div>")
    h.append(f"<div class='stat-card'><div class='stat-value'>{robot.get('processed_count',0)}</div><div class='stat-label'>Processed</div></div>")
    h.append(f"<div class='stat-card'><div class='stat-value'>{robot.get('failed_count',0)}</div><div class='stat-label'>Failed</div></div>")
    h.append(f"<div class='stat-card'><div class='stat-value'>{robot.get('idle_count',0)}</div><div class='stat-label'>Idle Count</div></div>")
    h.append("</div>")
    h.append("<table><tr><th>Robot ID</th><th>Status</th><th>Current Case</th><th>Current Run</th><th>Last Heartbeat</th><th>Message</th></tr>")
    h.append(
        f"<tr><td>{robot.get('robot_id') or '—'}</td>"
        f"<td class='status-{robot.get('status','idle')}'>{robot.get('status','idle')}</td>"
        f"<td>{robot.get('current_case_id') or '—'}</td>"
        f"<td>{robot.get('current_run_id') or '—'}</td>"
        f"<td>{robot.get('last_heartbeat_at') or '—'}</td>"
        f"<td>{robot.get('message','') or '—'}</td></tr>"
    )
    h.append("</table>")
    h.append("</div>")

    # --- Human Approval Inbox Summary ---
    h.append("<div id='approval-summary-panel'>")
    h.append("<h2>Human Approval Inbox</h2>")
    h.append("<div class='stat-grid'>")
    h.append(f"<div class='stat-card'><div class='stat-value status-pending'>{pending_approvals}</div><div class='stat-label'>Pending Approvals</div></div>")
    h.append(f"<div class='stat-card'><div class='stat-value'>{total_approvals}</div><div class='stat-label'>Total Approvals</div></div>")
    h.append("</div>")
    if pending_approvals > 0:
        h.append(f"<p><a href='/approvals/inbox'>View Approval Inbox</a> — {pending_approvals} task(s) awaiting review.</p>")
    else:
        h.append("<p><a href='/approvals/inbox'>View Approval Inbox</a> — No pending approvals.</p>")
    h.append("</div>")

    # --- Queue Summary ---
    h.append("<div id='queue-summary-panel'>")
    h.append("<h2>Queue Summary</h2>")
    h.append("<div class='stat-grid'>")
    h.append(f"<div class='stat-card'><div class='stat-value'>{queue['pending']}</div><div class='stat-label'>Pending</div></div>")
    h.append(f"<div class='stat-card'><div class='stat-value status-in_progress'>{queue['in_progress']}</div><div class='stat-label'>In Progress</div></div>")
    h.append(f"<div class='stat-card'><div class='stat-value status-completed'>{queue['completed']}</div><div class='stat-label'>Completed</div></div>")
    h.append(f"<div class='stat-card'><div class='stat-value status-failed'>{queue.get('failed',0)}</div><div class='stat-label'>Failed</div></div>")
    h.append(f"<div class='stat-card'><div class='stat-value'>{queue['total']}</div><div class='stat-label'>Total</div></div>")
    h.append("</div>")
    h.append("<p class='refresh-note'><a href='/erp/work-queue'>ERP Work Queue</a> · <a href='/simulation/dashboard'>Simulation Overview</a> · <a href='/approvals/approved-pending-writeback'>Approved Pending ERP Writeback</a></p>")
    h.append("</div>")

    # --- Injection Panel ---
    h.append("<div id='injection-panel'>")
    h.append("<h2>Manual Case Injection Panel</h2>")
    h.append("<div class='erp-quick-link' style='margin: 10px 0; padding: 10px; "
             "border: 2px solid #1a3a5c; background: #e3f2fd; font-size: 14px;'>"
             "<b>Legacy ERP Work Queue:</b> "
             "<a href='http://localhost:8002/erp/work-queue' style='font-size: 14px; font-weight: bold;'>"
             "http://localhost:8002/erp/work-queue</a>"
             " &mdash; stable ctl00_MainContent_btnOpenFirstPending entry for UiPath Robot"
             "</div>")
    h.append("<p class='refresh-note'>Use these controls to add cases to the processing queue. UiPath Robot picks up queued cases in Worker Mode. Proposal rows appear only after UiPath completes runs and Run Memory reaches the configured threshold.</p>")

    # Show last injection result if query params present (server-rendered fallback).
    h.append("<div id='injection-result'>")
    if injected_scenario and injected_count is not None:
        h.append(
            f"<div class='inject-result'>"
            f"Injected {injected_count} <b>{injected_scenario}</b> case(s) into pending queue. "
            f"<a href='/monitoring/live'>dismiss</a>"
            f"</div>"
        )
    h.append("</div>")

    h.append("<div class='inject-buttons'>")
    inject_scenarios = [
        ("normal", "Inject Normal Case"),
        ("budget_exceeded", "Inject Budget Exceeded"),
        ("vendor_info_missing", "Inject Vendor Missing"),
        ("inventory_shortage", "Inject Inventory Shortage"),
        ("ambiguous", "Inject Ambiguous Case"),
    ]
    for scenario_value, button_label in inject_scenarios:
        h.append(
            f"<form method='post' action='/simulation/inject-form' style='display:inline;' "
            f"class='inject-form' data-scenario='{scenario_value}'>"
            f"<input type='hidden' name='scenario' value='{scenario_value}'>"
            f"<input type='hidden' name='count' value='1'>"
            f"<button type='submit' class='inject-btn inject-{scenario_value}'>{button_label}</button>"
            f"</form>"
        )
    h.append("</div>")
    h.append("<div class='demo-injection-grid'>")
    demo_injections = [
        {
            "scenario": "agent_context_review",
            "count": 1,
            "title": "Agent Review + Enterprise Context",
            "body": (
                "One budget exception. The route agent reads /company-context, "
                "uses finance, sales, and operations context, then creates a web approval task."
            ),
            "expected": "Expected route: WAITING_FOR_HUMAN_APPROVAL",
        },
        {
            "scenario": "capex_budget_exception",
            "count": proposal_threshold(),
            "title": "API Proposal Seed Set",
            "body": (
                "Three CAPEX budget exceptions using business_action "
                "request_capex_budget_exception_approval. UiPath must process all runs before "
                "Pattern Memory can reach threshold."
            ),
            "expected": "Expected after 3 real commits: API_MODERNIZATION_PROPOSAL",
        },
        {
            "scenario": "inventory_shortage",
            "count": proposal_threshold(),
            "title": "XAML Workflow Proposal Seed Set",
            "body": (
                "Three inventory shortage cases. The agent flags a capability gap; repeated "
                "Run Memory evidence becomes an XAML_WORKFLOW_PROPOSAL."
            ),
            "expected": "Expected after 3 real commits: XAML_WORKFLOW_PROPOSAL",
        },
    ]
    for item in demo_injections:
        scenario_value = item["scenario"]
        count_value = int(item["count"])
        h.append("<div class='demo-injection-card'>")
        h.append(f"<h3>{item['title']}</h3>")
        h.append(f"<p>{item['body']}</p>")
        h.append(f"<p class='refresh-note'>{item['expected']}</p>")
        h.append(
            f"<form method='post' action='/simulation/inject-form' "
            f"class='inject-form' data-scenario='{scenario_value}'>"
            f"<input type='hidden' name='scenario' value='{scenario_value}'>"
            f"<input type='hidden' name='count' value='{count_value}'>"
            f"<button type='submit' class='inject-btn inject-{scenario_value}'>"
            f"Inject {count_value} Case{'s' if count_value != 1 else ''}</button>"
            f"</form>"
        )
        h.append("</div>")
    h.append("</div>")
    h.append("</div>")

    # --- Latest Cases ---
    h.append("<div id='latest-cases-panel'>")
    h.append("<h2>Latest Events / Audit Log</h2>")
    h.append("<table><tr><th>Sim ID</th><th>Case ID</th><th>PO ID</th><th>Scenario</th><th>Status</th><th>Run ID</th><th>Result</th><th>Final Route</th><th>Dashboard</th></tr>")
    for c in latest_cases[:10]:
        h.append(
            f"<tr><td>{c['sim_id']}</td><td>{c['case_id']}</td>"
            f"<td>{c['po_id']}</td>"
            f"<td>{html_lib.escape(str(c.get('scenario') or c.get('demo_purpose') or ''))}</td>"
            f"<td class='status-{c['status']}'>{c['status']}</td>"
            f"<td>{c['run_id']}</td><td>{c['result']}</td><td>{c['final_route']}</td>"
            f"<td><a href='{c['dashboard_url']}'>view</a></td></tr>"
        )
    h.append("</table>")
    h.append("</div>")

    # --- Real Run Memory ---
    h.append("<div id='real-run-memory-panel'>")
    h.append("<h2>Real Run Memory / Proposal Links</h2>")
    h.append("<div class='stat-grid'>")
    h.append(f"<div class='stat-card'><div class='stat-value'>{len(latest_runs)}</div><div class='stat-label'>Recent Runs</div></div>")
    h.append(f"<div class='stat-card'><div class='stat-value'>{len(patterns)}</div><div class='stat-label'>Patterns</div></div>")
    h.append(f"<div class='stat-card'><div class='stat-value'>{len(proposals)}</div><div class='stat-label'>Proposals</div></div>")
    h.append("</div>")
    h.append("<h3 class='subsection-title'>Pattern Threshold Progress</h3>")
    if pattern_progress:
        h.append("<table class='legacy-grid compact-progress'><thead><tr><th>Pattern</th><th>Observed / Threshold</th><th>Next Step</th></tr></thead><tbody>")
        for p in pattern_progress[:5]:
            h.append(
                f"<tr><td>{html_lib.escape(str(p.get('process_signature') or '—'))}</td>"
                f"<td><strong>{html_lib.escape(str(p.get('progress_label') or '0/0'))}</strong></td>"
                f"<td>{html_lib.escape(str(p.get('recommended_next_step') or '—'))}</td></tr>"
            )
        h.append("</tbody></table>")
    else:
        h.append("<p class='refresh-note'>No real Pattern Memory yet. Inject cases, let UiPath process them, then watch observed_count advance toward threshold.</p>")
    h.append("<p class='refresh-note'>Detailed memory and proposal evidence lives in Simulation Dashboard, Case Dashboard, and Proposal Inbox.</p>")
    h.append("<p><a href='/simulation/dashboard'>Open Simulation Overview</a> · <a href='/proposals/inbox'>Open Proposal Inbox</a></p>")
    h.append("</div>")

    # --- Proposal Inbox link panel (kept as compact DOM target for JS compatibility) ---
    h.append("<div id='proposal-inbox-panel'>")
    h.append("<h2>Proposal Inbox</h2>")
    h.append(f"<p>{len(proposals)} real proposal(s). <a href='/proposals/inbox'>View details</a></p>")
    h.append("</div>")

    # --- Audit Log ---
    h.append("<div id='audit-log-panel'>")
    h.append("<h2>Audit Log — Case Route Summary</h2>")
    h.append("<div class='stat-grid'>")
    for label, count in audit_counts.items():
        h.append(f"<div class='stat-card'><div class='stat-value'>{count}</div><div class='stat-label'>{label}</div></div>")
    h.append("</div>")
    h.append("</div>")

    # --- Heartbeat History ---
    h.append("<div id='heartbeat-history-panel'>")
    if heartbeat_history:
        h.append("<h2>Heartbeat History (Last 10)</h2>")
        h.append("<table><tr><th>Timestamp</th><th>Status</th><th>Case</th><th>Run</th><th>Message</th></tr>")
        for hb in heartbeat_history:
            h.append(
                f"<tr><td>{hb.get('timestamp','')}</td>"
                f"<td class='status-{hb.get('status','idle')}'>{hb.get('status','')}</td>"
                f"<td>{hb.get('current_case_id') or '—'}</td>"
                f"<td>{hb.get('current_run_id') or '—'}</td>"
                f"<td>{hb.get('message','') or '—'}</td></tr>"
            )
        h.append("</table>")
    h.append("</div>")

    # --- Footer ---
    h.append("<hr>")
    h.append("<p><a href='/simulation/dashboard'>Simulation Dashboard</a> · <a href='/simulation/state'>State JSON</a> · <a href='/robot/status'>Robot JSON</a> · <a href='/proposals/inbox'>Proposal Inbox</a> · <a href='/approvals/inbox'>Approval Inbox</a></p>")
    h.append("<p class='refresh-note'>Live monitor is read-only except explicit injection controls. No Codex calls, XAML changes, API deployments, or trusted registrations are automatic.</p>")

    # Client-side polling + injection intercept script (passed to the legacy shell).
    js: list[str] = []
    js.append("<script>")
    js.append("(function(){")
    js.append("  var POLL_INTERVAL_MS = 3000;")
    js.append("  var errorEl = document.getElementById('refresh-error');")
    js.append("  var lastUpdatedEl = document.getElementById('last-updated');")
    js.append("  var pollingEl = document.getElementById('polling-indicator');")
    js.append("  function escapeHtml(s){ if(s===null||s===undefined){return '';} return String(s).replace(/[&<>\"']/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;','\\'':'&#39;'}[ch];}); }")
    js.append("  function setText(el, text){ if(el){ el.textContent = text; } }")
    js.append("  function renderRobotStatus(d){")
    js.append("    var panel = document.getElementById('robot-status-panel');")
    js.append("    if(!panel){ return; }")
    js.append("    var r = d.robot_status || {};")
    js.append("    var status = r.status || 'idle';")
    js.append("    var cards = panel.querySelectorAll('.stat-card .stat-value');")
    js.append("    if(cards[0]){ cards[0].textContent = status; cards[0].className = 'stat-value status-' + status; }")
    js.append("    if(cards[1]){ cards[1].textContent = r.processed_count || 0; }")
    js.append("    if(cards[2]){ cards[2].textContent = r.failed_count || 0; }")
    js.append("    if(cards[3]){ cards[3].textContent = r.idle_count || 0; }")
    js.append("    var rows = panel.querySelectorAll('table tr');")
    js.append("    if(rows[1]){ rows[1].innerHTML = '<td>' + escapeHtml(r.robot_id || '—') + '</td>' + '<td class=\"status-' + status + '\">' + escapeHtml(status) + '</td>' + '<td>' + escapeHtml(r.current_case_id || '—') + '</td>' + '<td>' + escapeHtml(r.current_run_id || '—') + '</td>' + '<td>' + escapeHtml(r.last_heartbeat_at || '—') + '</td>' + '<td>' + escapeHtml(r.message || '—') + '</td>'; }")
    js.append("  }")
    js.append("  function renderApprovalSummary(d){")
    js.append("    var panel = document.getElementById('approval-summary-panel');")
    js.append("    if(!panel){ return; }")
    js.append("    var a = d.approvals_summary || {};")
    js.append("    var cards = panel.querySelectorAll('.stat-card .stat-value');")
    js.append("    if(cards[0]){ cards[0].textContent = a.pending || 0; }")
    js.append("    if(cards[1]){ cards[1].textContent = a.total || 0; }")
    js.append("    var p = panel.querySelector('p a');")
    js.append("    if(p){")
    js.append("      var link = p.getAttribute('href');")
    js.append("      var pending = a.pending || 0;")
    js.append("      p.parentNode.innerHTML = '<a href=\"' + link + '\">View Approval Inbox</a> — ' + (pending > 0 ? pending + ' task(s) awaiting review.' : 'No pending approvals.');")
    js.append("    }")
    js.append("  }")
    js.append("  function renderQueueSummary(d){")
    js.append("    var panel = document.getElementById('queue-summary-panel');")
    js.append("    if(!panel){ return; }")
    js.append("    var q = d.queue_summary || {};")
    js.append("    var cards = panel.querySelectorAll('.stat-card .stat-value');")
    js.append("    if(cards[0]){ cards[0].textContent = q.pending || 0; }")
    js.append("    if(cards[1]){ cards[1].textContent = q.in_progress || 0; }")
    js.append("    if(cards[2]){ cards[2].textContent = q.completed || 0; }")
    js.append("    if(cards[3]){ cards[3].textContent = q.failed || 0; }")
    js.append("    if(cards[4]){ cards[4].textContent = q.total || 0; }")
    js.append("  }")
    js.append("  function renderLatestCases(d){")
    js.append("    var panel = document.getElementById('latest-cases-panel');")
    js.append("    if(!panel){ return; }")
    js.append("    var cases = d.latest_cases || [];")
    js.append("    var table = panel.querySelector('table');")
    js.append("    if(!table){ return; }")
    js.append("    var html = '<tr><th>Sim ID</th><th>Case ID</th><th>PO ID</th><th>Scenario</th><th>Status</th><th>Run ID</th><th>Result</th><th>Final Route</th><th>Dashboard</th></tr>';")
    js.append("    for(var i=0;i<Math.min(cases.length, 10);i++){ var c = cases[i];")
    js.append("      html += '<tr><td>' + escapeHtml(c.sim_id) + '</td><td>' + escapeHtml(c.case_id) + '</td><td>' + escapeHtml(c.po_id) + '</td><td>' + escapeHtml(c.scenario || c.demo_purpose || '') + '</td><td class=\"status-' + escapeHtml(c.status) + '\">' + escapeHtml(c.status) + '</td><td>' + escapeHtml(c.run_id) + '</td><td>' + escapeHtml(c.result) + '</td><td>' + escapeHtml(c.final_route) + '</td><td><a href=\"' + escapeHtml(c.dashboard_url) + '\">view</a></td></tr>';")
    js.append("    }")
    js.append("    table.innerHTML = html;")
    js.append("  }")
    js.append("  function renderRunMemory(d){")
    js.append("    var panel = document.getElementById('real-run-memory-panel');")
    js.append("    if(!panel){ return; }")
    js.append("    var runs = d.latest_runs || [];")
    js.append("    var patterns = d.pattern_counts || [];")
    js.append("    var progress = d.pattern_progress || [];")
    js.append("    var props = d.proposal_inbox || [];")
    js.append("    var html = '<h2>Real Run Memory / Proposal Links</h2><div class=\"stat-grid\">';")
    js.append("    html += '<div class=\"stat-card\"><div class=\"stat-value\">' + runs.length + '</div><div class=\"stat-label\">Recent Runs</div></div>';")
    js.append("    html += '<div class=\"stat-card\"><div class=\"stat-value\">' + patterns.length + '</div><div class=\"stat-label\">Patterns</div></div>';")
    js.append("    html += '<div class=\"stat-card\"><div class=\"stat-value\">' + props.length + '</div><div class=\"stat-label\">Proposals</div></div>';")
    js.append("    html += '</div><p class=\"refresh-note\">Detailed memory and proposal evidence lives in Simulation Dashboard, Case Dashboard, and Proposal Inbox.</p>';")
    js.append("    html += '<h3 class=\"subsection-title\">Pattern Threshold Progress</h3>';")
    js.append("    if(progress.length){ html += '<table class=\"legacy-grid compact-progress\"><thead><tr><th>Pattern</th><th>Observed / Threshold</th><th>Next Step</th></tr></thead><tbody>';")
    js.append("      for(var i=0;i<Math.min(progress.length,5);i++){ var p = progress[i]; html += '<tr><td>' + escapeHtml(p.process_signature || '—') + '</td><td><strong>' + escapeHtml(p.progress_label || '0/0') + '</strong></td><td>' + escapeHtml(p.recommended_next_step || '—') + '</td></tr>'; }")
    js.append("      html += '</tbody></table>'; } else { html += '<p class=\"refresh-note\">No real Pattern Memory yet. Inject cases, let UiPath process them, then watch observed_count advance toward threshold.</p>'; }")
    js.append("    html += '<p><a href=\"/simulation/dashboard\">Open Simulation Overview</a> · <a href=\"/proposals/inbox\">Open Proposal Inbox</a></p>';")
    js.append("    panel.innerHTML = html;")
    js.append("  }")
    js.append("  function renderProposalInbox(d){")
    js.append("    var panel = document.getElementById('proposal-inbox-panel');")
    js.append("    if(!panel){ return; }")
    js.append("    var props = d.proposal_inbox || [];")
    js.append("    var html = '<h2>Proposal Inbox</h2><p>' + props.length + ' real proposal(s). <a href=\"/proposals/inbox\">View details</a></p>';")
    js.append("    panel.innerHTML = html;")
    js.append("  }")
    js.append("  function renderAuditLog(d){")
    js.append("    var panel = document.getElementById('audit-log-panel');")
    js.append("    if(!panel){ return; }")
    js.append("    var counts = (d.audit_log && d.audit_log.counts) || {};")
    js.append("    var html = '<h2>Audit Log — Case Route Summary</h2><div class=\"stat-grid\">';")
    js.append("    var keys = ['normal','exception','waiting','manual','capability_gap'];")
    js.append("    for(var i=0;i<keys.length;i++){ var k = keys[i];")
    js.append("      html += '<div class=\"stat-card\"><div class=\"stat-value\">' + escapeHtml(counts[k] || 0) + '</div><div class=\"stat-label\">' + escapeHtml(k) + '</div></div>';")
    js.append("    }")
    js.append("    html += '</div>';")
    js.append("    panel.innerHTML = html;")
    js.append("  }")
    js.append("  function renderHeartbeatHistory(d){")
    js.append("    var panel = document.getElementById('heartbeat-history-panel');")
    js.append("    if(!panel){ return; }")
    js.append("    var hist = d.heartbeat_history || [];")
    js.append("    var html = '';")
    js.append("    if(hist.length){")
    js.append("      html = '<h2>Heartbeat History (Last 10)</h2><table><tr><th>Timestamp</th><th>Status</th><th>Case</th><th>Run</th><th>Message</th></tr>';")
    js.append("      for(var i=0;i<hist.length;i++){ var hb = hist[i]; var status = hb.status || 'idle';")
    js.append("        html += '<tr><td>' + escapeHtml(hb.timestamp) + '</td><td class=\"status-' + escapeHtml(status) + '\">' + escapeHtml(hb.status) + '</td><td>' + escapeHtml(hb.current_case_id || '—') + '</td><td>' + escapeHtml(hb.current_run_id || '—') + '</td><td>' + escapeHtml(hb.message || '—') + '</td></tr>';")
    js.append("      }")
    js.append("      html += '</table>';")
    js.append("    }")
    js.append("    panel.innerHTML = html;")
    js.append("  }")
    js.append("  function refreshMonitoringData(){")
    js.append("    fetch('/monitoring/live-data', {headers: {'Accept': 'application/json'}, cache: 'no-store'})")
    js.append("      .then(function(resp){ if(!resp.ok){ throw new Error('HTTP ' + resp.status); } return resp.json(); })")
    js.append("      .then(function(d){")
    js.append("        renderRobotStatus(d);")
    js.append("        renderApprovalSummary(d);")
    js.append("        renderQueueSummary(d);")
    js.append("        renderLatestCases(d);")
    js.append("        renderRunMemory(d);")
    js.append("        renderProposalInbox(d);")
    js.append("        renderAuditLog(d);")
    js.append("        renderHeartbeatHistory(d);")
    js.append("        if(lastUpdatedEl){ lastUpdatedEl.textContent = 'Last updated: ' + (d.server_time || new Date().toISOString()); }")
    js.append("        if(pollingEl){ pollingEl.textContent = 'Live data polling: ON'; pollingEl.style.color = ''; }")
    js.append("        if(errorEl){ errorEl.style.display = 'none'; errorEl.textContent = ''; }")
    js.append("      })")
    js.append("      .catch(function(err){")
    js.append("        if(pollingEl){ pollingEl.textContent = 'Live data polling: ERROR'; pollingEl.style.color = '#7a1f1f'; }")
    js.append("        if(errorEl){ errorEl.style.display = 'block'; errorEl.textContent = 'Monitoring data refresh failed: ' + err.message; }")
    js.append("      });")
    js.append("  }")
    js.append("  // Intercept injection form submissions so we don't do a full page reload.")
    js.append("  function setupInjectionIntercept(){")
    js.append("    var forms = document.querySelectorAll('form.inject-form');")
    js.append("    for(var i=0;i<forms.length;i++){")
    js.append("      forms[i].addEventListener('submit', function(ev){")
    js.append("        ev.preventDefault();")
    js.append("        var scenario = this.getAttribute('data-scenario') || '';")
    js.append("        var fd = new FormData(this);")
    js.append("        var resultEl = document.getElementById('injection-result');")
    js.append("        fetch('/simulation/inject-form', {method: 'POST', body: fd, redirect: 'manual'})")
    js.append("          .then(function(resp){")
    js.append("            // /simulation/inject-form normally returns 303. With redirect:'manual',")
    js.append("            // an opaque redirect response is returned. We treat any 3xx / 2xx as success.")
    js.append("            if(resp.ok || resp.type === 'opaqueredirect' || (resp.status >= 300 && resp.status < 400)){")
    js.append("              var count = fd.get('count') || 1;")
    js.append("              if(resultEl){ resultEl.innerHTML = '<div class=\"inject-result\">Injected ' + count + ' <b>' + scenario + '</b> case(s) into pending queue.</div>'; }")
    js.append("              refreshMonitoringData();")
    js.append("              return;")
    js.append("            }")
    js.append("            throw new Error('HTTP ' + resp.status);")
    js.append("          })")
    js.append("          .catch(function(err){")
    js.append("            if(resultEl){ resultEl.innerHTML = '<div class=\"inject-result\" style=\"border-color:#7a1f1f;color:#7a1f1f;\">Injection failed: ' + err.message + '</div>'; }")
    js.append("          });")
    js.append("      });")
    js.append("    }")
    js.append("  }")
    js.append("  // Initial setup: kick off polling and intercept.")
    js.append("  setupInjectionIntercept();")
    js.append("  setInterval(refreshMonitoringData, POLL_INTERVAL_MS);")
    js.append("})();")
    js.append("</script>")
    script = "\n".join(js)
    extra_css = """
.demo-injection-grid { display: grid; grid-template-columns: repeat(3, minmax(220px, 1fr)); gap: 10px; margin-top: 12px; }
.demo-injection-card { border: 1px solid #9aa8b8; background: #f8fafc; padding: 10px; min-height: 160px; display: grid; grid-template-rows: auto 1fr auto auto; gap: 6px; }
.demo-injection-card h3 { margin: 0; font-size: 13px; color: #102a47; }
.demo-injection-card p { margin: 0; line-height: 1.4; white-space: normal; }
.demo-injection-card form { margin-top: 4px; }
.demo-injection-card button { width: 100%; min-height: 32px; }
.subsection-title { margin: 8px 0 5px; font-size: 12px; color: #304258; }
.compact-progress th, .compact-progress td { font-size: 11px; white-space: normal; }
@media (max-width: 980px) { .demo-injection-grid { grid-template-columns: 1fr; } }
"""

    html = _render_legacy_shell(
        active_tab="Monitoring",
        title="Live Monitoring",
        breadcrumb="Home &gt; Monitoring &gt; Live Monitoring",
        content_html="\n".join(h),
        screen_id="MON-LIVE-301",
        extra_css=extra_css,
        extra_script=script,
    )
    return HTMLResponse(content=html)


# ===========================================================================
# Web-based Human Approval Inbox + Audit Trail
#
# Approval tasks are created from real runs/cases (not static demo data).
# The approval lifecycle is: PENDING -> APPROVED / REJECTED.
# Approve/reject appends a HUMAN_APPROVAL_COMPLETED / HUMAN_APPROVAL_REJECTED
# event to the corresponding Run Memory, but does NOT call Codex, deploy APIs,
# modify XAML, or register trusted capabilities.
# ===========================================================================


class ApprovalCreateRequest(BaseModel):
    """Input for ``POST /approvals/create``."""
    case_id: str
    po_id: str | None = None
    run_id: str | None = None
    simulation_case_id: str | None = None
    amount: float | None = None
    budget_limit: float | None = None
    erp_status: str | None = None
    raw_exception_text: str | None = None
    business_remarks: str | None = None
    agent_reasoning_summary: str | None = None
    company_context_reference: dict[str, Any] | None = None
    company_context_snapshot: dict[str, Any] | None = None
    policy_gate_reason: str | None = None
    agent_recommendation: str | None = None
    reason: str = ""
    policy_decision: str = "REQUIRE_HUMAN_APPROVAL"
    requested_by: str = "system"


def _create_approval_task(payload: ApprovalCreateRequest) -> dict[str, Any]:
    """Create and store a PENDING approval task without extra side effects."""
    from memory.run_memory import _utc_iso

    approval_id = _generate_approval_id()
    now = _utc_iso()
    task = {
        "approval_id": approval_id,
        "case_id": payload.case_id,
        "po_id": payload.po_id,
        "run_id": payload.run_id,
        "simulation_case_id": payload.simulation_case_id,
        "amount": payload.amount,
        "budget_limit": payload.budget_limit,
        "erp_status": payload.erp_status,
        "raw_exception_text": payload.raw_exception_text,
        "business_remarks": payload.business_remarks,
        "agent_reasoning_summary": payload.agent_reasoning_summary,
        "company_context_reference": payload.company_context_reference or {},
        "company_context_snapshot": payload.company_context_snapshot or {},
        "policy_gate_reason": payload.policy_gate_reason,
        "agent_recommendation": payload.agent_recommendation,
        "reason": payload.reason,
        "policy_decision": payload.policy_decision,
        "requested_by": payload.requested_by,
        "status": "PENDING",
        "decision": None,
        "approver": None,
        "comment": None,
        "created_at": now,
        "approved_at": None,
        "audit_trail": [
            {
                "action": "APPROVAL_CREATED",
                "timestamp": now,
                "actor": payload.requested_by,
                "detail": f"Approval task created for case {payload.case_id}.",
            }
        ],
    }
    _APPROVAL_TASKS[approval_id] = task
    return task


def _active_approval_for_simulation_case(
    simulation_case_id: str,
) -> dict[str, Any] | None:
    """Return an existing approval that is still part of the writeback flow."""
    active_statuses = {
        "PENDING",
        "APPROVED_PENDING_ERP_WRITEBACK",
        "ERP_WRITEBACK_IN_PROGRESS",
        "ERP_WRITEBACK_COMPLETED",
    }
    for task in _APPROVAL_TASKS.values():
        if (
            task.get("simulation_case_id") == simulation_case_id
            and task.get("status") in active_statuses
        ):
            return task
    return None






@app.post("/approvals/create", tags=["Approvals"])
def approvals_create(payload: ApprovalCreateRequest) -> dict[str, Any]:
    """Create a human approval task.

    The task starts as PENDING. Does NOT auto-approve, execute business
    writeback, create proposals, call Codex, or modify XAML.
    """
    task = _create_approval_task(payload)
    return {
        "approval_id": task["approval_id"],
        "status": "PENDING",
        "approval_url": f"/approvals/{task['approval_id']}",
        "inbox_url": "/approvals/inbox",
        "created_at": task["created_at"],
    }


@app.get("/approvals/inbox", tags=["Approvals"])
def approvals_inbox(request: Request) -> HTMLResponse:
    """HTML page showing all approval tasks with Approve/Reject forms.

    PENDING tasks show interactive forms; APPROVED/REJECTED tasks show
    the decision, approver, and comment for audit.
    """
    tasks = _list_approvals()

    # Build content HTML (the legacy shell provides head/style/top nav/menu/footer).
    c: list[str] = []
    approval_result = request.query_params.get("approval_result")
    approval_id = request.query_params.get("approval_id")
    if approval_result and approval_id:
        result_label = "approved" if approval_result == "approved" else "rejected"
        c.append(
            "<div class='first-pending-banner'>"
            f"Approval {html_lib.escape(approval_id)} was {html_lib.escape(result_label)}. "
            "The inbox has been updated."
            "</div>"
        )
    pending_count = _pending_approval_count()
    c.append(f"<p class='meta'>{pending_count} pending · {len(tasks)} total · "
             f"<a href='/monitoring/live'>Monitoring</a> · "
             f"<a href='/simulation/dashboard'>Simulation Dashboard</a> · "
             f"<a href='/erp/work-queue'>ERP Work Queue</a></p>")

    if not tasks:
        c.append("<p>No approval tasks yet. Create one via POST /approvals/create.</p>")
    else:
        for t in tasks:
            status_val = t.get("status", "PENDING")
            css_class = status_val.lower()
            c.append(f"<div class='approval-card {css_class}'>")

            # Header.
            c.append(f"<h3>{t['approval_id']} — "
                     f"<span class='status-{status_val}'>{status_val}</span></h3>")
            c.append("<table>")
            c.append(f"<tr><th>Case ID</th><td>{t.get('case_id','')}</td>"
                     f"<th>PO ID</th><td>{t.get('po_id','—')}</td></tr>")
            c.append(f"<tr><th>Run ID</th><td>{t.get('run_id') or '—'}</td>"
                     f"<th>Sim ID</th><td>{t.get('simulation_case_id') or '—'}</td></tr>")
            c.append(f"<tr><th>Amount</th><td>{t.get('amount','—')}</td>"
                     f"<th>Budget Limit</th><td>{t.get('budget_limit','—')}</td></tr>")
            c.append(f"<tr><th>System Message</th><td colspan='3'>{html_lib.escape(str(t.get('raw_exception_text') or t.get('reason') or '—'))}</td></tr>")
            c.append(f"<tr><th>Business Remarks</th><td colspan='3'>{html_lib.escape(str(t.get('business_remarks') or '—'))}</td></tr>")
            c.append(f"<tr><th>Agent Recommendation</th><td colspan='3'>{html_lib.escape(str(t.get('agent_recommendation') or t.get('policy_decision') or '—'))}</td></tr>")
            c.append(f"<tr><th>Agent Reasoning Summary</th><td colspan='3'>{html_lib.escape(str(t.get('agent_reasoning_summary') or '—'))}</td></tr>")
            context_ref = t.get("company_context_reference") or {}
            context_snapshot = t.get("company_context_snapshot") or {}
            context_text = json.dumps(context_ref or context_snapshot, ensure_ascii=False)
            c.append(f"<tr><th>Company Context Snapshot</th><td colspan='3'>{html_lib.escape(context_text if context_text not in ('{}', 'null') else '—')}</td></tr>")
            c.append(f"<tr><th>Policy Gate Reason</th><td colspan='3'>{html_lib.escape(str(t.get('policy_gate_reason') or t.get('reason') or '—'))}</td></tr>")
            c.append(f"<tr><th>Reason</th><td colspan='3'>{t.get('reason','')}</td></tr>")
            c.append(f"<tr><th>Policy Decision</th><td>{t.get('policy_decision','')}</td>"
                     f"<th>Requested By</th><td>{t.get('requested_by','')}</td></tr>")
            c.append(f"<tr><th>Created At</th><td>{t.get('created_at','')}</td>"
                     f"<th>Approved At</th><td>{t.get('approved_at') or '—'}</td></tr>")

            # Decision info if not pending.
            if status_val != "PENDING":
                c.append(f"<tr><th>Decision</th><td class='status-{status_val}'>{t.get('decision','')}</td>"
                         f"<th>Approver</th><td>{t.get('approver') or '—'}</td></tr>")
                c.append(f"<tr><th>Comment</th><td colspan='3'>{t.get('comment') or '—'}</td></tr>")

            # Dashboard link.
            case_id = t.get("case_id", "")
            run_id = t.get("run_id") or ""
            if run_id:
                dash = f"<a href='/case-dashboard/{case_id}?run_id={run_id}'>Case Dashboard</a>"
            else:
                dash = f"<a href='/case-dashboard/{case_id}'>Case Dashboard</a>"
            c.append(f"<tr><th>Links</th><td colspan='3'>{dash} · "
                     f"<a href='/approvals/{t['approval_id']}'>JSON</a></td></tr>")
            c.append("</table>")

            # Forms for PENDING tasks.
            if status_val == "PENDING":
                c.append("<div class='approval-actions'>")
                c.append("<form class='approval-decision-form' method='post' action='/approvals/{}/approve'>".format(t["approval_id"]))
                c.append("<label>Approver <input type='text' name='approver' placeholder='Approver name' required></label>")
                c.append("<label>Comment <textarea name='comment' placeholder='Approval comment'></textarea></label>")
                c.append("<button type='submit' class='btn-approve'>Approve</button>")
                c.append("</form>")
                c.append("<form class='approval-decision-form' method='post' action='/approvals/{}/reject'>".format(t["approval_id"]))
                c.append("<label>Approver <input type='text' name='approver' placeholder='Approver name' required></label>")
                c.append("<label>Reason <textarea name='comment' placeholder='Rejection reason'></textarea></label>")
                c.append("<button type='submit' class='btn-reject'>Reject</button>")
                c.append("</form>")
                c.append("</div>")

            # Audit trail.
            audit = t.get("audit_trail", [])
            if audit:
                c.append("<div class='audit'><b>Audit Trail:</b><ul>")
                for entry in audit:
                    c.append(f"<li>[{entry.get('timestamp','')}] <b>{entry.get('action','')}</b> "
                             f"by {entry.get('actor','')} — {entry.get('detail','')}</li>")
                c.append("</ul></div>")

            c.append("</div>")

    c.append("<hr><p class='meta'>Approval decisions and comments are recorded in the audit trail.</p>")

    extra_css = """
.approval-actions { display: grid; grid-template-columns: repeat(2, minmax(260px, 1fr)); gap: 10px; margin-top: 10px; align-items: stretch; }
.approval-decision-form { display: grid; grid-template-columns: 1fr; gap: 8px; padding: 10px; border: 1px solid #9aa8b8; background: #f8fbff; min-height: 180px; }
.approval-decision-form label { display: grid; gap: 4px; font-weight: 700; color: #22364d; }
.approval-decision-form input,
.approval-decision-form textarea { box-sizing: border-box; width: 100%; min-height: 32px; border: 1px solid #8fa0b3; padding: 5px 6px; font: inherit; background: #fff; }
.approval-decision-form textarea { min-height: 58px; resize: vertical; }
.approval-decision-form button { width: 100%; min-height: 32px; align-self: end; }
@media (max-width: 900px) { .approval-actions { grid-template-columns: 1fr; } }
"""

    html = _render_legacy_shell(
        active_tab="Approvals",
        title="Human Approval Inbox",
        breadcrumb="Home &gt; Approvals &gt; Human Approval Inbox",
        content_html="\n".join(c),
        screen_id="APPROVAL-INBOX-201",
        extra_css=extra_css,
    )
    return HTMLResponse(content=html)


@app.get("/approvals/approved-pending-writeback", tags=["Approvals"])
def approvals_approved_pending_writeback() -> dict[str, Any]:
    """List all approval tasks awaiting ERP writeback.

    Returns approvals with status=APPROVED_PENDING_ERP_WRITEBACK.
    Each entry includes the ERP detail URL so the Robot can navigate
    to the ERP page and click the appropriate button.
    """
    tasks = [
        t for t in _list_approvals()
        if t.get("status") == "APPROVED_PENDING_ERP_WRITEBACK"
    ]
    items = []
    for t in tasks:
        sim_id = t.get("simulation_case_id") or ""
        erp_detail_url = f"/erp/work-queue/{sim_id}" if sim_id else ""
        items.append({
            "approval_id": t.get("approval_id"),
            "simulation_case_id": sim_id,
            "case_id": t.get("case_id"),
            "po_id": t.get("po_id"),
            "run_id": t.get("run_id"),
            "approval_url": f"/approvals/{t.get('approval_id')}",
            "erp_detail_url": erp_detail_url,
            "status": t.get("status"),
        })
    return {"items": items}


@app.get("/approvals/{approval_id}", tags=["Approvals"])
def approvals_get(approval_id: str) -> dict[str, Any]:
    """Return a single approval task as JSON."""
    task = _APPROVAL_TASKS.get(approval_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval task not found: {approval_id}",
        )
    return dict(task)


async def _parse_approver_comment(request) -> tuple[str, str]:
    """Parse approver and comment from either form or JSON request."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        return body.get("approver", ""), body.get("comment", "")
    form = await request.form()
    return form.get("approver", ""), form.get("comment", "")


def _request_is_json(request: Request) -> bool:
    """Return true when the approval endpoint was called as an API."""
    return "application/json" in request.headers.get("content-type", "")


def _approval_decision_redirect(result: dict[str, Any]) -> RedirectResponse:
    """Redirect browser form submissions back to the approval inbox."""
    from urllib.parse import urlencode

    params = urlencode(
        {
            "approval_result": str(result["decision"]).lower(),
            "approval_id": str(result["approval_id"]),
        }
    )
    return RedirectResponse(
        url=f"/approvals/inbox?{params}",
        status_code=status.HTTP_303_SEE_OTHER,
    )



async def _process_approval_decision(
    request,
    approval_id: str,
    decision: str,
) -> dict[str, Any]:
    """Shared logic for approve and reject endpoints."""
    from memory.run_memory import _utc_iso

    approval = _APPROVAL_TASKS.get(approval_id)
    if approval is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval task not found: {approval_id}",
        )

    if approval["status"] != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Approval {approval_id} is already {approval['status']}, cannot change.",
        )

    approver, comment = await _parse_approver_comment(request)
    if not approver:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'approver' field is required.",
        )

    now = _utc_iso()
    # Approve sets status=APPROVED_PENDING_ERP_WRITEBACK (awaiting ERP writeback).
    # Reject sets status=REJECTED. The decision field records the human decision.
    if decision == "APPROVED":
        approval["status"] = "APPROVED_PENDING_ERP_WRITEBACK"
    else:
        approval["status"] = decision
    approval["decision"] = decision
    approval["approver"] = approver
    approval["comment"] = comment
    approval["approved_at"] = now

    # Append audit record.
    audit_entry = {
        "action": f"APPROVAL_{decision}",
        "timestamp": now,
        "actor": approver,
        "detail": f"Approval {decision.lower()} by {approver}. Comment: {comment or '(none)'}",
    }
    approval.setdefault("audit_trail", []).append(audit_entry)

    # Append Run Memory event (best-effort, never blocks).
    event_type = "HUMAN_APPROVAL_COMPLETED" if decision == "APPROVED" else "HUMAN_APPROVAL_REJECTED"
    memory_result = _append_approval_event_to_run_memory(
        approval, event_type, decision, approver, comment
    )

    return {
        "approval_id": approval_id,
        "status": approval["status"],
        "decision": decision,
        "approver": approver,
        "comment": comment,
        "approved_at": now,
        "run_memory_event": memory_result,
        "codex_called": False,
        "xaml_modified": False,
        "api_deployed": False,
        "trusted_capability_registered": False,
    }


@app.post("/approvals/{approval_id}/approve", tags=["Approvals"])
async def approvals_approve(request: Request, approval_id: str):
    """Approve a pending approval task.

    Accepts both form-encoded and JSON input. Sets status=APPROVED,
    records approver/comment/approved_at, appends a HUMAN_APPROVAL_COMPLETED
    event to Run Memory (if run_id exists).

    Does NOT call Codex, modify XAML, deploy APIs, or register trusted
    capabilities.
    """
    result = await _process_approval_decision(request, approval_id, "APPROVED")
    if _request_is_json(request):
        return result
    return _approval_decision_redirect(result)


@app.post("/approvals/{approval_id}/reject", tags=["Approvals"])
async def approvals_reject(request: Request, approval_id: str):
    """Reject a pending approval task.

    Accepts both form-encoded and JSON input. Sets status=REJECTED,
    records approver/comment/approved_at, appends a HUMAN_APPROVAL_REJECTED
    event to Run Memory (if run_id exists).

    Does NOT call Codex, modify XAML, deploy APIs, or register trusted
    capabilities.
    """
    result = await _process_approval_decision(request, approval_id, "REJECTED")
    if _request_is_json(request):
        return result
    return _approval_decision_redirect(result)


# ---------------------------------------------------------------------------
# Approval → ERP writeback tracking
#
# After a human approves a task, the status becomes APPROVED_PENDING_ERP_WRITEBACK.
# The UiPath Robot (or a human via the ERP page) must then explicitly perform
# the ERP writeback and call mark-writeback-started / mark-writeback-completed.
# This ensures the approval POST is NOT disguised as an ERP confirmation.
# ---------------------------------------------------------------------------



@app.post("/approvals/{approval_id}/mark-writeback-started", tags=["Approvals"])
async def approvals_mark_writeback_started(
    request: Request,
    approval_id: str,
) -> dict[str, Any]:
    """Mark that the ERP writeback has been started (Robot is processing).

    Transitions status from APPROVED_PENDING_ERP_WRITEBACK to ERP_WRITEBACK_IN_PROGRESS.
    Appends an audit record. Does NOT call Codex, modify XAML, or deploy APIs.
    Does NOT write ERP status — the writeback-completed step handles that.
    """
    from memory.run_memory import _utc_iso

    approval = _APPROVAL_TASKS.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval task not found: {approval_id}")
    if approval["status"] != "APPROVED_PENDING_ERP_WRITEBACK":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Approval {approval_id} status is {approval['status']}, "
                   f"expected APPROVED_PENDING_ERP_WRITEBACK.")

    # Accept optional robot_id from JSON or form.
    robot_id = ""
    content_type = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in content_type:
            body = await request.json()
            robot_id = body.get("robot_id", "")
        else:
            form = await request.form()
            robot_id = form.get("robot_id", "")
    except Exception:
        pass

    now = _utc_iso()
    approval["status"] = "ERP_WRITEBACK_IN_PROGRESS"
    audit_entry = {
        "action": "ERP_WRITEBACK_IN_PROGRESS",
        "timestamp": now,
        "actor": robot_id or "system",
        "detail": f"ERP writeback started by {robot_id or 'system'}.",
    }
    approval.setdefault("audit_trail", []).append(audit_entry)

    return {
        "approval_id": approval_id,
        "status": "ERP_WRITEBACK_IN_PROGRESS",
        "robot_id": robot_id or "system",
        "codex_called": False,
        "xaml_modified": False,
        "api_deployed": False,
        "trusted_capability_registered": False,
    }


@app.post("/approvals/{approval_id}/mark-writeback-completed", tags=["Approvals"])
async def approvals_mark_writeback_completed(
    request: Request,
    approval_id: str,
) -> dict[str, Any]:
    """Mark that the ERP writeback has been completed.

    Transitions status from ERP_WRITEBACK_IN_PROGRESS (or APPROVED_PENDING_ERP_WRITEBACK)
    to ERP_WRITEBACK_COMPLETED. Appends audit record and a best-effort
    ERP_WRITEBACK_COMPLETED event to Run Memory.

    If the approval has a simulation_case_id, the corresponding simulation case
    is updated to erp_status=ERP_APPROVAL_REQUESTED, last_action=SUBMIT_APPROVAL_REQUEST
    (the canonical writeback result for an approved task).

    Input (JSON or form):
        erp_action: e.g. "SUBMIT_APPROVAL_REQUEST_CLICKED"
        robot_id: e.g. "UIPATH-LOCAL-001"

    Does NOT call Codex, modify XAML, or deploy APIs.
    """
    from memory.run_memory import _utc_iso

    approval = _APPROVAL_TASKS.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval task not found: {approval_id}")
    if approval["status"] not in ("APPROVED_PENDING_ERP_WRITEBACK", "ERP_WRITEBACK_IN_PROGRESS"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Approval {approval_id} status is {approval['status']}, "
                   f"expected APPROVED_PENDING_ERP_WRITEBACK or ERP_WRITEBACK_IN_PROGRESS.")

    erp_action = ""
    robot_id = ""
    content_type = (request.headers.get("content-type") or "").lower()
    try:
        if "application/json" in content_type:
            body = await request.json()
            erp_action = body.get("erp_action", "")
            robot_id = body.get("robot_id", "")
        else:
            form = await request.form()
            erp_action = form.get("erp_action", "")
            robot_id = form.get("robot_id", "")
    except Exception:
        pass

    now = _utc_iso()
    approval["status"] = "ERP_WRITEBACK_COMPLETED"
    audit_entry = {
        "action": "ERP_WRITEBACK_COMPLETED",
        "timestamp": now,
        "actor": robot_id or "system",
        "detail": f"ERP writeback completed by {robot_id or 'system'}. "
                  f"erp_action={erp_action or '(none)'}.",
    }
    approval.setdefault("audit_trail", []).append(audit_entry)

    # Sync the simulation case erp_status/last_action if linked.
    sim_id = approval.get("simulation_case_id") or ""
    sim_case_updated = False
    if sim_id:
        sim_case = _find_simulation_case(sim_id)
        if sim_case is not None:
            sim_case["erp_status"] = "ERP_APPROVAL_REQUESTED"
            sim_case["last_action"] = "SUBMIT_APPROVAL_REQUEST"
            sim_case["last_action_at"] = now
            sim_case_updated = True

    memory_result = _append_erp_writeback_event_to_run_memory(
        approval, "ERP_WRITEBACK_COMPLETED", erp_action, robot_id or "system"
    )

    return {
        "approval_id": approval_id,
        "status": "ERP_WRITEBACK_COMPLETED",
        "erp_action": erp_action,
        "robot_id": robot_id or "system",
        "simulation_case_id": sim_id,
        "simulation_case_updated": sim_case_updated,
        "run_memory_event": memory_result,
        "codex_called": False,
        "xaml_modified": False,
        "api_deployed": False,
        "trusted_capability_registered": False,
    }


# ===========================================================================
# ERP-style Work Queue — Legacy ERP Purchase Order Work Queue page.
#
# These endpoints provide an ERP-style UI that the UiPath Robot can interact
# with via screen scraping / selectors. The pages use stable legacy-style
# DOM ids (ctl00_MainContent_lbl*) for reliable UiPath selector targeting.
#
# ERP action buttons only update the simulation case's erp_status and
# last_action — they do NOT write Run Memory, create proposals, call Codex,
# modify XAML, or deploy APIs.
# ====================================================================================




# ERP action → (erp_status, last_action label) mapping.
_ERP_ACTIONS: dict[str, dict[str, str]] = {
    "mark-standard-processed": {
        "erp_status": "ERP_STANDARD_PROCESSED",
        "last_action": "MARK_STANDARD_PROCESSED",
    },
    "mark-waiting-vendor": {
        "erp_status": "ERP_WAITING_VENDOR_INFO",
        "last_action": "MARK_WAITING_VENDOR",
    },
    "flag-capability-gap": {
        "erp_status": "ERP_CAPABILITY_GAP_FLAGGED",
        "last_action": "FLAG_CAPABILITY_GAP",
    },
    "send-manual-investigation": {
        "erp_status": "ERP_MANUAL_INVESTIGATION_REQUIRED",
        "last_action": "SEND_MANUAL_INVESTIGATION",
    },
    "submit-approval-request": {
        "erp_status": "ERP_APPROVAL_REQUESTED",
        "last_action": "SUBMIT_APPROVAL_REQUEST",
    },
}


def _process_erp_action(simulation_case_id: str, action_key: str) -> dict[str, Any]:
    """Shared logic for all 5 ERP action button endpoints.

    Updates erp_status and last_action on the simulation case.
    Submit Approval Request also creates/reuses a human approval task.
    Does NOT write Run Memory, create proposals, or call Codex.
    """
    from memory.run_memory import _utc_iso

    case = _find_simulation_case(simulation_case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation case not found: {simulation_case_id}")

    action = _ERP_ACTIONS[action_key]
    now = _utc_iso()
    case["erp_status"] = action["erp_status"]
    case["last_action"] = action["last_action"]
    case["last_action_at"] = now

    response = {
        "simulation_case_id": simulation_case_id,
        "case_id": case.get("case_id"),
        "po_id": case.get("po_id"),
        "erp_status": case["erp_status"],
        "last_action": case["last_action"],
        "last_action_at": now,
        "run_memory_written": False,
        "proposal_created": False,
        "codex_called": False,
        "xaml_modified": False,
        "api_deployed": False,
        "trusted_capability_registered": False,
    }

    if action_key == "submit-approval-request":
        approval = _active_approval_for_simulation_case(simulation_case_id)
        approval_created = False
        if approval is None:
            approval = _create_approval_task(
                ApprovalCreateRequest(
                    case_id=str(case.get("case_id") or ""),
                    po_id=case.get("po_id"),
                    run_id=case.get("run_id"),
                    simulation_case_id=simulation_case_id,
                    amount=case.get("amount"),
                    budget_limit=case.get("budget_limit"),
                    erp_status=case.get("erp_status"),
                    raw_exception_text=case.get("raw_exception_text"),
                    business_remarks=case.get("business_remarks"),
                    agent_reasoning_summary=case.get("agent_reasoning_summary"),
                    company_context_reference=case.get("company_context_reference"),
                    company_context_snapshot=case.get("company_context_snapshot"),
                    policy_gate_reason=case.get("policy_gate_reason"),
                    agent_recommendation=case.get("final_route"),
                    reason=(
                        "ERP Submit Approval Request action was clicked for "
                        f"{case.get('po_id') or simulation_case_id}."
                    ),
                    policy_decision=(
                        str(case.get("policy_decision") or "REQUIRE_HUMAN_APPROVAL")
                    ),
                    requested_by="erp_work_queue",
                )
            )
            approval_created = True

        case["approval_id"] = approval.get("approval_id")
        response.update({
            "approval_created": approval_created,
            "approval_id": approval.get("approval_id"),
            "approval_status": approval.get("status"),
            "approval_inbox_url": "/approvals/inbox",
        })

    return response


@app.get("/erp/work-queue", tags=["ERP Work Queue"], response_class=HTMLResponse)
def erp_work_queue() -> HTMLResponse:
    """Legacy ERP Purchase Order Work Queue page.

    Displays all simulation cases in an ERP-style WebForms table. Pending and
    waiting cases are shown first. Provides a stable ctl00_MainContent_btnOpenFirstPending
    entry point for UiPath selectors, plus per-row Open links with WebForms
    GridView-style ids (ctl00_MainContent_grdPoWorkQueue_ctlNN_btnOpen).

    Read-only — does not write memory, create proposals, or modify XAML.
    """
    cases = _SIMULATION_QUEUE.get("cases", [])

    # Sort: pending/waiting first, then in_progress, then completed/failed.
    status_order = {"pending": 0, "waiting": 0, "in_progress": 1, "completed": 2, "failed": 2}
    sorted_cases = sorted(
        enumerate(cases),
        key=lambda pair: (status_order.get(pair[1].get("status", ""), 3), pair[0]),
    )

    # Find first pending/waiting case for the stable btnOpenFirstPending entry.
    first_pending_case = None
    for _, c in sorted_cases:
        if c.get("status", "") in ("pending", "waiting"):
            first_pending_case = c
            break
    first_pending_sim_id = first_pending_case.get("simulation_case_id", "") if first_pending_case else ""

    # Build content HTML (inner part of the shell).
    c: list[str] = []
    c.append(f"<p class='legacy-note'>Total cases: {len(cases)} &middot; "
             f"<a href='/monitoring/live'>Back to Monitoring</a> &middot; "
             f"<a href='/approvals/inbox'>Approval Inbox</a> &middot; "
             f"<a href='/approvals/approved-pending-writeback'>Approved Pending ERP Writeback</a></p>")
    c.append("<p class='legacy-note' style='font-style:italic;'>This is a legacy-style ERP work queue "
             "mirrored from real simulation queue for UiPath UI automation proof.</p>")

    # --- Stable ctl00_MainContent_btnOpenFirstPending entry (only if pending/waiting exists) ---
    if first_pending_case is not None:
        c.append(f"<div class='first-pending-banner'>")
        c.append(f"<a id='ctl00_MainContent_btnOpenFirstPending' class='button' "
                 f"href='/erp/work-queue/{first_pending_sim_id}'>Open First Pending Case</a>")
        c.append(f" &mdash; stable entry for UiPath selector (points to {first_pending_sim_id})")
        c.append(f"</div>")

    # --- Work queue table ---
    c.append("<div class='legacy-panel'>")
    c.append("<h2>Open Purchase Order Work Items</h2>")
    c.append("<div class='legacy-panel-body'>")

    if not cases or first_pending_case is None:
        c.append("<div id='ctl00_MainContent_lblQueueEmptyMessage' class='queue-empty'>"
                 "No pending ERP work item.</div>")
    c.append("<table id='ctl00_MainContent_grdPoWorkQueue' class='legacy-grid'>")
    c.append("<thead><tr>")
    c.append("<th>Sim ID</th><th>Case ID</th><th>PO Number</th><th>Amount</th>"
             "<th>Budget Limit</th><th>Scenario</th><th>ERP Status</th>"
             "<th>Simulation Status</th><th>Last Action</th><th>Open</th>")
    c.append("</tr></thead><tbody>")
    if not cases:
        c.append("<tr><td colspan='10'>No work queue rows.</td></tr>")
    else:
        for row_idx, (_, case) in enumerate(sorted_cases, start=2):
            sim_id = case.get("simulation_case_id", "")
            case_id = case.get("case_id", "")
            po_id = case.get("po_id", "")
            amount = case.get("amount", "")
            budget_limit = case.get("budget_limit", "")
            scenario = case.get("scenario", "")
            erp_status = case.get("erp_status", "")
            sim_status = case.get("status", "")
            last_action = case.get("last_action") or "—"
            row_ctl = f"ctl{row_idx:02d}"

            if sim_status in ("pending", "waiting"):
                status_cls = "status-pending"
            elif sim_status in ("completed",):
                status_cls = "status-normal"
            elif sim_status in ("failed",):
                status_cls = "status-exception"
            else:
                status_cls = "status-erp"

            c.append(
                f"<tr class='sim-{sim_status}'>"
                f"<td>{sim_id}</td>"
                f"<td>{case_id}</td>"
                f"<td>{po_id}</td>"
                f"<td>{amount}</td>"
                f"<td>{budget_limit}</td>"
                f"<td>{scenario}</td>"
                f"<td class='status-erp'>{erp_status or '—'}</td>"
                f"<td class='{status_cls}'>{sim_status}</td>"
                f"<td>{last_action}</td>"
                f"<td><a id='ctl00_MainContent_grdPoWorkQueue_{row_ctl}_btnOpen' "
                f"data-uipath-role='open-case' class='button' "
                f"href='/erp/work-queue/{sim_id}'>Open</a></td>"
                f"</tr>"
            )
    c.append("</tbody></table>")

    c.append("</div></div>")  # legacy-panel-body + legacy-panel

    c.append("<div class='legacy-note'>")
    c.append("Legacy ERP Work Queue — data from the current simulation queue. "
             "Open a purchase order to review details, update case status, and continue the workflow.")
    c.append("</div>")

    html = _render_legacy_shell(
        active_tab="Procurement",
        title="Legacy ERP Purchase Order Work Queue",
        breadcrumb="Home &gt; Procurement &gt; Purchase Order Work Queue",
        content_html="\n".join(c),
        screen_id="PROC-EXC-204",
    )
    return HTMLResponse(content=html)


@app.get("/erp/work-queue/{simulation_case_id}", tags=["ERP Work Queue"], response_class=HTMLResponse)
def erp_work_queue_detail(simulation_case_id: str) -> HTMLResponse:
    """Legacy ERP detail page for a single purchase order case.

    Uses stable WebForms-style DOM ids (ctl00_MainContent_lbl* for fields,
    ctl00_MainContent_btn* for action buttons) so UiPath selectors can
    reliably target fields and buttons.

    Provides 5 ERP action buttons that POST to the corresponding endpoints.
    ERP actions only update erp_status/last_action — no Run Memory, no
    proposals, no Codex, no XAML modification.

    When a user or RPA opens this page, the simulation case is automatically
    claimed (pending → in_progress) so that /simulation/cases/complete can
    later close it. This is idempotent for cases already in_progress and
    is a no-op for completed/failed cases.
    """
    # Claim the case (pending → in_progress) before rendering.
    claim_simulation_case(simulation_case_id)
    case = _find_simulation_case(simulation_case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation case not found: {simulation_case_id}")

    sim_id = case.get("simulation_case_id", "")
    case_id = case.get("case_id", "")
    po_id = case.get("po_id", "")
    amount = case.get("amount", "")
    budget_limit = case.get("budget_limit", "")
    vendor_id = case.get("vendor_id") or "—"
    scenario = case.get("scenario", "")
    exception_reason = case.get("raw_exception_text", "") or "—"
    business_remarks = case.get("business_remarks") or "Routine office purchase. No exception noted."
    erp_status = case.get("erp_status", "")
    sim_status = case.get("status", "")
    run_id = case.get("run_id") or "—"
    final_route = case.get("final_route") or "—"
    policy_decision = case.get("policy_decision") or "—"
    last_action = case.get("last_action") or "—"

    c: list[str] = []
    c.append(f"<p class='legacy-note'><a href='/erp/work-queue'>&laquo; Back to Work Queue</a> &middot; "
             f"<a href='/monitoring/live'>Monitoring</a></p>")

    # --- Business detail fields with stable WebForms-style ids ---
    c.append("<div class='legacy-panel'>")
    c.append("<h2>Purchase Order Processing</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append("<div class='erp-order-summary'>")
    c.append("<table class='form-table order-fields'>")
    c.append(f"<tr><th>PO Number</th><td><span id='ctl00_MainContent_lblPoNumber'>{po_id}</span></td></tr>")
    c.append(f"<tr><th>Amount</th><td><span id='ctl00_MainContent_lblAmount'>{amount}</span></td></tr>")
    c.append(f"<tr><th>Budget Limit</th><td><span id='ctl00_MainContent_lblBudgetLimit'>{budget_limit}</span></td></tr>")
    c.append(f"<tr><th>Vendor ID</th><td><span id='ctl00_MainContent_lblVendorId'>{vendor_id}</span></td></tr>")
    c.append(f"<tr><th>ERP Status</th><td><span id='ctl00_MainContent_lblErpStatus'>{erp_status}</span></td></tr>")
    c.append(f"<tr><th>System Message</th><td><span id='ctl00_MainContent_lblExceptionReason'>{exception_reason}</span></td></tr>")
    c.append(f"<tr><th>Business Remarks</th><td><span id='ctl00_MainContent_lblBusinessRemarks'>{business_remarks}</span></td></tr>")
    c.append("</table>")
    c.append("</div>")
    c.append("</div></div>")

    # --- Technical metadata kept for UiPath selector stability ---
    c.append("<details class='legacy-panel technical-audit'>")
    c.append("<summary>Technical Audit / RPA Metadata</summary>")
    c.append("<div class='legacy-panel-body'>")
    c.append("<table class='form-table audit-fields'>")
    c.append(f"<tr><th>Simulation Case ID</th><td><span id='ctl00_MainContent_lblSimulationCaseId'>{sim_id}</span></td></tr>")
    c.append(f"<tr><th>Case ID</th><td><span id='ctl00_MainContent_lblCaseId'>{case_id}</span></td></tr>")
    c.append(f"<tr><th>Scenario</th><td><span id='ctl00_MainContent_lblScenario'>{scenario}</span></td></tr>")
    c.append(f"<tr><th>Simulation Status</th><td><span id='ctl00_MainContent_lblSimulationStatus'>{sim_status}</span></td></tr>")
    c.append(f"<tr><th>Run ID</th><td><span id='ctl00_MainContent_lblRunId'>{run_id}</span></td></tr>")
    c.append(f"<tr><th>Final Route</th><td><span id='ctl00_MainContent_lblFinalRoute'>{final_route}</span></td></tr>")
    c.append(f"<tr><th>Policy Decision</th><td><span id='ctl00_MainContent_lblPolicyDecision'>{policy_decision}</span></td></tr>")
    c.append(f"<tr><th>Last Action</th><td><span id='ctl00_MainContent_lblLastAction'>{last_action}</span></td></tr>")
    c.append("</table>")
    c.append("</div>")
    c.append("</details>")

    # --- ERP Action Buttons with WebForms-style ids ---
    c.append("<div class='legacy-panel'>")
    c.append("<h2>ERP Actions</h2>")
    c.append("<div class='legacy-panel-body'>")
    c.append("<div class='actions'>")
    c.append(f"<form method='post' action='/erp/work-queue/{sim_id}/mark-standard-processed-form'>"
             f"<button id='ctl00_MainContent_btnMarkStandardProcessed' type='submit' class='button'>"
             f"Mark Standard Processed</button></form>")
    c.append(f"<form method='post' action='/erp/work-queue/{sim_id}/mark-waiting-vendor-form'>"
             f"<button id='ctl00_MainContent_btnMarkWaitingVendor' type='submit' class='button'>"
             f"Mark Waiting Vendor</button></form>")
    c.append(f"<form method='post' action='/erp/work-queue/{sim_id}/flag-capability-gap-form'>"
             f"<button id='ctl00_MainContent_btnFlagCapabilityGap' type='submit' class='button'>"
             f"Flag Capability Gap</button></form>")
    c.append(f"<form method='post' action='/erp/work-queue/{sim_id}/send-manual-investigation-form'>"
             f"<button id='ctl00_MainContent_btnSendManualInvestigation' type='submit' class='button'>"
             f"Send Manual Investigation</button></form>")
    c.append(f"<form method='post' action='/erp/work-queue/{sim_id}/submit-approval-request-form'>"
             f"<button id='ctl00_MainContent_btnSubmitApprovalRequest' type='submit' class='button'>"
             f"Submit Approval Request</button></form>")
    c.append("</div>")
    c.append("</div></div>")

    c.append("<div class='legacy-note'>")
    c.append("Legacy ERP Detail Page — use the action buttons to update the purchase order workflow status. "
             "Human approval and ERP write-back confirmation are tracked from the approval queue.")
    c.append("</div>")

    extra_css = """
.erp-order-summary { max-width: 980px; }
.order-fields th { width: 170px; }
.order-fields td { white-space: normal; line-height: 1.45; font-size: 13px; }
.order-fields tr:nth-child(6) td,
.order-fields tr:nth-child(7) td { background: #fbfcfe; }
.technical-audit { font-size: 11px; color: #4e5d70; }
.technical-audit summary { cursor: pointer; padding: 7px 9px; background: linear-gradient(#f8fafc, #e7edf5); border-bottom: 1px solid #9aa8b8; font-weight: 700; color: #304258; }
.technical-audit .legacy-panel-body { padding: 8px; }
.audit-fields th, .audit-fields td { font-size: 11px; color: #4e5d70; }
"""

    html = _render_legacy_shell(
        active_tab="Procurement",
        title=f"Purchase Order — {po_id}",
        breadcrumb=f"Home &gt; Procurement &gt; Purchase Order Detail &gt; {po_id}",
        content_html="\n".join(c),
        screen_id="PROC-EXC-DET-205",
        extra_css=extra_css,
    )
    return HTMLResponse(content=html)


@app.post("/erp/work-queue/{simulation_case_id}/mark-standard-processed", tags=["ERP Work Queue"])
def erp_mark_standard_processed(simulation_case_id: str) -> dict[str, Any]:
    """ERP button: Mark case as standard-processed."""
    return _process_erp_action(simulation_case_id, "mark-standard-processed")


@app.post("/erp/work-queue/{simulation_case_id}/mark-waiting-vendor", tags=["ERP Work Queue"])
def erp_mark_waiting_vendor(simulation_case_id: str) -> dict[str, Any]:
    """ERP button: Mark case as waiting for vendor info."""
    return _process_erp_action(simulation_case_id, "mark-waiting-vendor")


@app.post("/erp/work-queue/{simulation_case_id}/flag-capability-gap", tags=["ERP Work Queue"])
def erp_flag_capability_gap(simulation_case_id: str) -> dict[str, Any]:
    """ERP button: Flag a capability gap."""
    return _process_erp_action(simulation_case_id, "flag-capability-gap")


@app.post("/erp/work-queue/{simulation_case_id}/send-manual-investigation", tags=["ERP Work Queue"])
def erp_send_manual_investigation(simulation_case_id: str) -> dict[str, Any]:
    """ERP button: Send for manual investigation."""
    return _process_erp_action(simulation_case_id, "send-manual-investigation")


@app.post("/erp/work-queue/{simulation_case_id}/submit-approval-request", tags=["ERP Work Queue"])
def erp_submit_approval_request(simulation_case_id: str) -> dict[str, Any]:
    """ERP button: Submit an approval request (triggers human approval flow)."""
    return _process_erp_action(simulation_case_id, "submit-approval-request")


# ---------------------------------------------------------------------------
# Browser form-redirect variants: execute the action, then redirect back to
# the ERP work queue HTML page (instead of leaving the browser on raw JSON).
# The original JSON endpoints above are kept for API callers.
# ---------------------------------------------------------------------------

@app.post("/erp/work-queue/{simulation_case_id}/mark-standard-processed-form", tags=["ERP Work Queue"])
def erp_mark_standard_processed_form(simulation_case_id: str):
    """Browser form variant: execute action, redirect to /erp/work-queue."""
    _process_erp_action(simulation_case_id, "mark-standard-processed")
    return RedirectResponse(url="/erp/work-queue", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/erp/work-queue/{simulation_case_id}/mark-waiting-vendor-form", tags=["ERP Work Queue"])
def erp_mark_waiting_vendor_form(simulation_case_id: str):
    """Browser form variant: execute action, redirect to /erp/work-queue."""
    _process_erp_action(simulation_case_id, "mark-waiting-vendor")
    return RedirectResponse(url="/erp/work-queue", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/erp/work-queue/{simulation_case_id}/flag-capability-gap-form", tags=["ERP Work Queue"])
def erp_flag_capability_gap_form(simulation_case_id: str):
    """Browser form variant: execute action, redirect to /erp/work-queue."""
    _process_erp_action(simulation_case_id, "flag-capability-gap")
    return RedirectResponse(url="/erp/work-queue", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/erp/work-queue/{simulation_case_id}/send-manual-investigation-form", tags=["ERP Work Queue"])
def erp_send_manual_investigation_form(simulation_case_id: str):
    """Browser form variant: execute action, redirect to /erp/work-queue."""
    _process_erp_action(simulation_case_id, "send-manual-investigation")
    return RedirectResponse(url="/erp/work-queue", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/erp/work-queue/{simulation_case_id}/submit-approval-request-form", tags=["ERP Work Queue"])
def erp_submit_approval_request_form(simulation_case_id: str):
    """Browser form variant: execute action, redirect to /erp/work-queue."""
    _process_erp_action(simulation_case_id, "submit-approval-request")
    return RedirectResponse(url="/erp/work-queue", status_code=status.HTTP_303_SEE_OTHER)


# ===========================================================================
# Run Memory — structured, append-only memory that captures each UiPath execution
# as it happens. Each run produces raw artifacts -> normalized memory ->
# summary -> pattern update + capability-evolution decision. The Structured
# Run Memory (under ``memory/runs/``, ``memory/cases/``, ``memory/patterns/``,
# ``memory/proposals/``) is the system of record; LangGraph MemorySaver is
# only used as a transient agent-state checkpoint (PRD 17.5).
# ===========================================================================


# Proposal ID sequence state file under memory/proposals/.
_PROPOSAL_SEQ_FILE = "_sequence.json"
_CODEX_SESSION_LOCK = threading.Lock()


def _codex_cli_execution_mode() -> str:
    """Return ``mock`` or ``real`` for human-approved Codex handoff.

    Existing behavior is preserved: unless demo mode is explicitly enabled, the
    worker tries the real local ``codex exec`` path. Demo recordings can set
    ``CODEX_CLI_DEMO_MODE=mock`` or ``CODEX_CLI_EXECUTION_MODE=mock`` for a
    deterministic staged stream.
    """
    explicit = os.getenv("CODEX_CLI_EXECUTION_MODE", "").strip().lower()
    if explicit in {"mock", "demo", "mock_success"}:
        return "mock"
    if explicit in {"real", "live"}:
        return "real"

    demo_mode = os.getenv("CODEX_CLI_DEMO_MODE", "").strip().lower()
    if demo_mode in {"mock", "mock_success", "demo", "1", "true", "yes"}:
        return "mock"
    if demo_mode in {"real", "live", "0", "false", "off", "no"}:
        return "real"
    return "real"


def _codex_cli_mode_label(mode: str | None) -> str:
    return "Demo Mock Stream" if mode == "mock" else "Real Codex CLI"


def _brief_text(value: Any, *, max_len: int = 220) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "..."


def _codex_sessions_root() -> Path:
    from memory.run_memory import memory_root

    root = memory_root() / "codex_sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _codex_session_path(session_id: str) -> Path:
    return _codex_sessions_root() / f"{session_id}.json"


def _next_codex_session_id(proposal_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in proposal_id)
    with _CODEX_SESSION_LOCK:
        existing = sorted(_codex_sessions_root().glob(f"CODEX-{safe}-*.json"))
        return f"CODEX-{safe}-{len(existing) + 1:03d}"


def _read_codex_session(session_id: str) -> dict[str, Any] | None:
    from memory.run_memory import _read_json

    path = _codex_session_path(session_id)
    if not path.exists():
        return None
    return _read_json(path, {}) or None


def _write_codex_session(session: dict[str, Any]) -> None:
    from memory.run_memory import _write_json

    _write_json(_codex_session_path(str(session["session_id"])), session)


def _append_codex_activity(
    session_id: str,
    *,
    stage: str,
    title: str,
    detail: str,
    status_label: str = "running",
) -> None:
    from memory.run_memory import _utc_iso

    with _CODEX_SESSION_LOCK:
        session = _read_codex_session(session_id)
        if not session:
            return
        now = _utc_iso()
        events = list(session.get("activity_events", []) or [])
        events.append({
            "timestamp": now,
            "stage": stage,
            "title": title,
            "detail": detail,
            "status": status_label,
        })
        session["activity_events"] = events[-120:]
        session["current_activity"] = title
        session["activity_summary"] = detail
        session["updated_at"] = now
        _write_codex_session(session)


def _summarize_codex_line(line: str) -> str:
    stripped = (line or "").strip()
    if not stripped:
        return ""
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return _brief_text(stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return _brief_text(stripped)

    event_type = data.get("type") or data.get("event") or data.get("msg") or "codex_event"
    parts = [str(event_type)]
    for key in ("message", "role", "status", "name", "command", "path", "file", "delta", "content"):
        value = data.get(key)
        if value:
            parts.append(f"{key}={_brief_text(value, max_len=140)}")
    if len(parts) == 1:
        compact = {
            k: v
            for k, v in data.items()
            if k not in {"timestamp", "id"} and v not in (None, "", [], {})
        }
        parts.append(_brief_text(compact, max_len=180))
    return _brief_text(" | ".join(parts), max_len=260)


def _codex_session_display_steps(session: dict[str, Any]) -> list[dict[str, str]]:
    steps = [
        ("approval", "Human approval", "Proposal approved by reviewer"),
        ("context", "Read proposal evidence", "Run Memory and Pattern Memory loaded"),
        ("plan", "Plan code change", "Codex maps proposal into repo edits"),
        ("edit", "Apply local candidate", "Code artifacts are prepared for review"),
        ("verify", "Run verification", "Tests or static checks are evaluated"),
        ("handoff", "Prepare PR handoff", "Reviewer receives the candidate summary"),
    ]
    events = list(session.get("activity_events", []) or [])
    completed = {
        str(event.get("stage"))
        for event in events
        if str(event.get("status") or "").lower() in {"completed", "done", "success"}
    }
    seen = {str(event.get("stage")) for event in events}
    active_stage = str(events[-1].get("stage")) if events else "approval"
    status = str(session.get("status") or "").upper()
    display_steps: list[dict[str, str]] = []
    for stage, title, detail in steps:
        state = "pending"
        if stage in completed:
            state = "done"
        elif stage == active_stage and status not in {"COMPLETED", "FAILED"}:
            state = "active"
        elif stage in seen and status == "COMPLETED":
            state = "done"
        display_steps.append({
            "stage": stage,
            "title": title,
            "detail": detail,
            "state": state,
        })
    if status == "COMPLETED":
        for item in display_steps:
            if item["stage"] in seen or item["stage"] in {"approval", "context", "plan", "edit", "verify", "handoff"}:
                item["state"] = "done"
    if status == "FAILED":
        for item in display_steps:
            if item["stage"] == active_stage:
                item["state"] = "failed"
    return display_steps


def _append_codex_session_log(session_id: str, line: str) -> None:
    from memory.run_memory import _utc_iso

    with _CODEX_SESSION_LOCK:
        session = _read_codex_session(session_id)
        if not session:
            return
        logs = list(session.get("logs", []) or [])
        now = _utc_iso()
        summary = _summarize_codex_line(line)
        logs.append({"timestamp": now, "line": line, "summary": summary})
        session["logs"] = logs[-500:]
        if summary:
            session["last_raw_log_summary"] = summary
        session["updated_at"] = now
        _write_codex_session(session)


def _run_codex_session_worker(session_id: str) -> None:
    """Run Codex CLI after explicit human approval and stream logs to memory."""
    from memory.run_memory import _utc_iso

    session = _read_codex_session(session_id)
    if not session:
        return

    execution_mode = session.get("execution_mode") or _codex_cli_execution_mode()
    mock_mode = execution_mode == "mock"
    if mock_mode:
        with _CODEX_SESSION_LOCK:
            session = _read_codex_session(session_id) or session
            if not session:
                return
            session["status"] = "RUNNING"
            session["started_at"] = _utc_iso()
            session["command"] = "codex exec <proposal prompt> (demo mock stream)"
            session["current_activity"] = "Starting demo Codex stream"
            session["activity_summary"] = (
                "Mock mode is enabled for recording. No external Codex process is launched."
            )
            _write_codex_session(session)

        proposal_type = str(session.get("proposal_type") or "PROPOSAL")
        is_xaml = proposal_type.startswith("XAML")
        staged_events = [
            (
                "context",
                "Loaded proposal evidence",
                "Read Pattern Memory threshold evidence, latest Run Memory examples, business remarks, and agent reasoning summaries.",
                "completed",
                "memory.load proposal_id={proposal_id} source=run_memory+pattern_memory",
            ),
            (
                "plan",
                "Built implementation plan",
                (
                    "Selected a UiPath workflow candidate path for human review; existing Windows XAML files remain untouched."
                    if is_xaml
                    else "Selected a FastAPI candidate endpoint and schema changes; deployment and trusted registration remain disabled."
                ),
                "completed",
                f"codex.plan proposal_type={proposal_type} safety=no_auto_deploy,no_auto_xaml_modify",
            ),
            (
                "edit",
                "Prepared local candidate changes",
                (
                    "Generated a workflow proposal artifact and PR notes that describe selector-safe UiPath changes for review."
                    if is_xaml
                    else "Drafted service handler, request schema, audit write, and tests in the working tree for reviewer inspection."
                ),
                "completed",
                "files.prepare candidate_artifact=true draft_pr_notes=true",
            ),
            (
                "verify",
                "Ran verification checklist",
                "Checked guardrails: no API deployment, no trusted capability registration, no automatic approval, no Windows XAML overwrite.",
                "completed",
                "verify.guardrails api_deployed=false xaml_modified=false trusted_capability_registered=false",
            ),
            (
                "handoff",
                "Prepared draft PR handoff",
                "Codex session finished. Reviewer can inspect generated files and promote the candidate through normal PR review.",
                "completed",
                "pr.handoff status=ready_for_human_review auto_merge=false",
            ),
        ]
        for stage, title, detail, status_label, line in staged_events:
            _append_codex_activity(
                session_id,
                stage=stage,
                title=title,
                detail=detail,
                status_label=status_label,
            )
            _append_codex_session_log(session_id, line.format(proposal_id=session.get("proposal_id", "")))
            time.sleep(float(os.getenv("CODEX_CLI_MOCK_STEP_DELAY_SECONDS", "0.8")))
        with _CODEX_SESSION_LOCK:
            session = _read_codex_session(session_id) or session
            if not session:
                return
            session["status"] = "COMPLETED"
            session["return_code"] = 0
            session["completed_at"] = _utc_iso()
            session["current_activity"] = "Draft PR handoff ready"
            session["activity_summary"] = (
                "Demo stream completed. Candidate artifacts are ready for human code review."
            )
            session["draft_pr_created"] = False
            session["draft_pr_handoff_ready"] = True
            session["api_deployed"] = False
            session["xaml_modified"] = False
            session["trusted_capability_registered"] = False
            _write_codex_session(session)
        return

    codex_bin = shutil.which("codex")
    if not codex_bin:
        with _CODEX_SESSION_LOCK:
            session = _read_codex_session(session_id) or session
            session["status"] = "FAILED"
            session["return_code"] = 127
            session["completed_at"] = _utc_iso()
            session["error"] = "Codex CLI executable not found on PATH."
            session["current_activity"] = "Codex CLI not found"
            session["activity_summary"] = "Real mode was requested, but the codex executable is not available on PATH."
            _write_codex_session(session)
        _append_codex_activity(
            session_id,
            stage="handoff",
            title="Codex CLI not found",
            detail="Real mode was requested, but the codex executable is not available on PATH.",
            status_label="failed",
        )
        _append_codex_session_log(session_id, "Codex CLI executable not found on PATH.")
        return

    cmd = [
        codex_bin,
        "exec",
        "--cd",
        str(REPO_ROOT),
        "--sandbox",
        "workspace-write",
        "--json",
        session.get("codex_prompt", ""),
    ]
    with _CODEX_SESSION_LOCK:
        session = _read_codex_session(session_id) or session
        session["status"] = "RUNNING"
        session["started_at"] = _utc_iso()
        session["command"] = " ".join(cmd[:7] + ["<proposal prompt>"])
        session["current_activity"] = "Starting real Codex CLI"
        session["activity_summary"] = "Real local codex exec process is starting after explicit human approval."
        _write_codex_session(session)
    _append_codex_activity(
        session_id,
        stage="context",
        title="Starting real Codex CLI",
        detail="Launching local codex exec with the approved proposal prompt.",
        status_label="running",
    )
    _append_codex_session_log(session_id, "Starting Codex CLI: " + session["command"])

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            raw_line = line.rstrip()
            _append_codex_session_log(session_id, raw_line)
            summary = _summarize_codex_line(raw_line)
            if summary:
                _append_codex_activity(
                    session_id,
                    stage="edit",
                    title="Codex CLI event",
                    detail=summary,
                    status_label="running",
                )
        rc = proc.wait()
        with _CODEX_SESSION_LOCK:
            session = _read_codex_session(session_id) or session
            session["status"] = "COMPLETED" if rc == 0 else "FAILED"
            session["return_code"] = rc
            session["completed_at"] = _utc_iso()
            session["current_activity"] = "Codex CLI completed" if rc == 0 else "Codex CLI failed"
            session["activity_summary"] = (
                "Real Codex CLI process exited successfully. Review generated changes before PR handling."
                if rc == 0
                else "Real Codex CLI process exited with a non-zero status."
            )
            session["draft_pr_created"] = False
            session["draft_pr_handoff_ready"] = rc == 0
            session["api_deployed"] = False
            session["xaml_modified"] = False
            session["trusted_capability_registered"] = False
            _write_codex_session(session)
        _append_codex_activity(
            session_id,
            stage="handoff",
            title="Codex CLI completed" if rc == 0 else "Codex CLI failed",
            detail=(
                "Real Codex CLI process exited successfully. Review generated changes before PR handling."
                if rc == 0
                else "Real Codex CLI process exited with a non-zero status."
            ),
            status_label="completed" if rc == 0 else "failed",
        )
    except Exception as exc:  # pragma: no cover - defensive runtime path
        with _CODEX_SESSION_LOCK:
            session = _read_codex_session(session_id) or session
            session["status"] = "FAILED"
            session["return_code"] = None
            session["completed_at"] = _utc_iso()
            session["error"] = str(exc)
            session["current_activity"] = "Codex CLI failed to start"
            session["activity_summary"] = str(exc)
            _write_codex_session(session)
        _append_codex_activity(
            session_id,
            stage="handoff",
            title="Codex CLI failed to start",
            detail=str(exc),
            status_label="failed",
        )
        _append_codex_session_log(session_id, f"Codex CLI failed to start: {exc}")


class RunStartRequest(BaseModel):
    """Input for ``POST /memory/runs/start``."""

    case_id: str
    po_id: str | None = None
    workflow_name: str | None = "Main.xaml"
    source: str = "uipath"
    demo_mode: bool = True


class RunStartResponse(BaseModel):
    run_id: str
    case_id: str
    status: str
    memory_path: str
    started_at: str | None = None


class RunEventRequest(BaseModel):
    """Input for ``POST /memory/runs/{run_id}/events``."""

    event_type: str
    case_id: str | None = None
    po_id: str | None = None
    stage: str | None = None
    status: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RunEventResponse(BaseModel):
    run_id: str
    event_type: str
    occurred_at: str
    appended: bool = True


class RunArtifactRequest(BaseModel):
    """Input for ``POST /memory/runs/{run_id}/artifacts``."""

    artifact_type: str
    case_id: str | None = None
    data: dict[str, Any]


class RunArtifactResponse(BaseModel):
    run_id: str
    artifact_type: str
    file: str
    mode: str
    occurred_at: str | None = None
    updated_at: str | None = None


class RunCompleteRequest(BaseModel):
    """Input for ``POST /memory/runs/{run_id}/complete``."""

    case_id: str
    result: str
    final_stage: str
    execution_mode: str


class RunCompleteResponse(BaseModel):
    run_id: str
    case_id: str
    status: str
    completed_at: str


class RunCommitResponse(BaseModel):
    """Output for ``POST /memory/runs/{run_id}/commit``."""

    run_id: str
    case_id: str
    process_signature: str
    case_run_summary_created: bool
    pattern_updated: bool
    capability_evolution_decision: str
    proposal_id: str | None = None
    dashboard_url: str


def _next_proposal_id(decision_kind: str) -> str:
    """Allocate the next proposal id of the form ``PROP-{KIND}-NNNN``."""
    from memory.run_memory import proposals_root, _read_json, _write_json

    seq_path = proposals_root() / _PROPOSAL_SEQ_FILE
    seq = _read_json(seq_path, {})
    counters = seq if isinstance(seq, dict) else {}
    key = decision_kind.upper()
    next_n = int(counters.get(key, 0)) + 1
    counters[key] = next_n
    _write_json(seq_path, counters)
    return f"PROP-{key}-{next_n:04d}"


def _decision_kind(decision: str) -> str:
    """Map an evolution decision label to a proposal kind slug."""
    d = (decision or "").upper()
    if "API" in d:
        return "API"
    if "XAML_IMPROVEMENT" in d:
        return "XAMLI"
    if "XAML_WORKFLOW" in d:
        return "XAMLW"
    return "GEN"


def _build_codex_prompt(
    *,
    decision_label: str,
    business_action: str,
    exception_type: str,
    process_signature: str,
    recommended_change: str,
    evidence_run_ids: list[str],
    observed_count: int,
) -> str:
    """Build a Codex prompt for a proposal.

    The prompt is a string that describes what a coding agent should do.
    It is NOT executed — it is returned for human review and optional
    manual invocation. No XAML is modified, no API is deployed.
    """
    evidence_str = ", ".join(evidence_run_ids[:5]) if evidence_run_ids else "none"
    if decision_label == "API_MODERNIZATION_PROPOSAL":
        return (
            f"Generate a FastAPI endpoint for business_action "
            f"'{business_action}' (process_signature: {process_signature}). "
            f"Recommended change: {recommended_change}. "
            f"Evidence runs: {evidence_str} (observed_count={observed_count}). "
            "The endpoint must: (1) accept a purchase order ID, (2) create an "
            "approval task, (3) return a 202 with task_id, (4) log to audit. "
            "Prepare the local code change and a draft PR handoff summary. "
            "Do NOT deploy or register the endpoint — output code for human review only."
        )
    if decision_label == "XAML_WORKFLOW_PROPOSAL":
        return (
            f"Generate a UiPath XAML workflow candidate for "
            f"business_action '{business_action}' "
            f"(process_signature: {process_signature}). "
            f"Recommended change: {recommended_change}. "
            f"Evidence runs: {evidence_str} (observed_count={observed_count}). "
            "The workflow must: (1) handle the exception type, (2) call the "
            "appropriate business service, (3) write back the result. "
            "Prepare a proposal artifact and draft PR handoff summary. "
            "Do NOT modify existing Windows XAML files — output a new candidate for human review only."
        )
    if decision_label == "XAML_IMPROVEMENT_PROPOSAL":
        return (
            f"Generate improved selector strategy for existing workflow "
            f"handling business_action '{business_action}'. "
            f"Recommended change: {recommended_change}. "
            f"Evidence runs: {evidence_str} (observed_count={observed_count}). "
            "Do NOT modify existing XAML files — output suggestions for human review only."
        )
    return (
        f"Review and define remediation for business_action '{business_action}' "
        f"(process_signature: {process_signature}, observed_count={observed_count}). "
        f"Recommended change: {recommended_change}. "
        "Do NOT execute any changes — output recommendations for human review only."
    )


def _record_proposal(
    *,
    proposal_id: str,
    run_id: str,
    case_id: str,
    decision: dict[str, Any],
    pattern: dict[str, Any],
) -> dict[str, Any]:
    """Persist a proposal file under ``memory/proposals/{proposal_id}.json``.

    Proposals are recommendations only — they never auto-deploy an API or
    modify XAML, and never auto-promote to trusted capability (PRD safety
    boundary). Each proposal requires human review + validation + registration
    before reuse.

    Lifecycle (this round only creates the first two stages; no auto-advance):
      PROPOSAL_CREATED -> HUMAN_REVIEW_REQUIRED -> APPROVED_FOR_CANDIDATE_GENERATION
      -> CANDIDATE_GENERATED -> VALIDATION_PENDING -> VALIDATION_PASSED
      -> REGISTRATION_APPROVED -> TRUSTED
    """
    from memory.run_memory import proposal_path, _write_json, _utc_iso

    now = _utc_iso()
    decision_label = decision.get("decision") or "UNKNOWN_PROPOSAL"
    business_action = (
        decision.get("business_action") or pattern.get("business_action") or ""
    )
    exception_type = pattern.get("exception_type") or ""
    process_signature = pattern.get("process_signature") or (
        f"{business_action}__{exception_type}" if business_action and exception_type else ""
    )

    # Derive recommended_change text per proposal type.
    if decision_label == "API_MODERNIZATION_PROPOSAL":
        recommended_change = decision.get("recommended_api") or (
            "Create or validate API endpoint "
            "POST /api/purchase-orders/{po_id}/approval-request"
        )
    elif decision_label == "XAML_WORKFLOW_PROPOSAL":
        recommended_change = decision.get("recommended_change") or (
            f"Create new UiPath workflow candidate for {exception_type}"
        )
    elif decision_label == "XAML_IMPROVEMENT_PROPOSAL":
        recommended_change = decision.get("recommended_change") or (
            "Improve selector strategy and exception handling in existing workflow"
        )
    else:
        recommended_change = decision.get("recommended_change") or "Review and define remediation"

    # evidence_run_ids from the pattern's latest runs.
    evidence_run_ids = list(pattern.get("latest_run_ids", []))
    if run_id and run_id not in evidence_run_ids:
        evidence_run_ids.insert(0, run_id)

    # Threshold and codex_prompt for the proposal.
    threshold = decision.get("threshold") or proposal_threshold()
    codex_prompt = _build_codex_prompt(
        decision_label=decision_label,
        business_action=business_action,
        exception_type=exception_type,
        process_signature=process_signature,
        recommended_change=recommended_change,
        evidence_run_ids=evidence_run_ids,
        observed_count=pattern.get("observed_count", 0),
    )

    # Advisory risk factors (do not block proposal creation).
    risks = {
        "field_stability": pattern.get("field_stability", 0.0),
        "business_value": pattern.get("business_value", 0.0),
        "validation_pass_rate": (
            pattern.get("validation_pass_count", 0)
            / max(pattern.get("observed_count", 1), 1)
        ),
        "ui_fragility": pattern.get("ui_fragility", 0.0),
        "note": (
            "Risk factors are advisory; proposal was generated because the "
            "real-run observed_count reached the threshold. Review risks "
            "before approving for Codex prompt generation."
        ),
    }

    proposal = {
        "proposal_id": proposal_id,
        "proposal_type": decision_label,
        "run_id": run_id,
        "case_id": case_id,
        "decision": decision_label,
        "business_action": business_action,
        "exception_type": exception_type,
        "process_signature": process_signature,
        "evidence_run_ids": evidence_run_ids,
        "observed_count": pattern.get("observed_count"),
        "threshold": threshold,
        "recommended_change": recommended_change,
        "risks": risks,
        "validation_required": True,
        "codex_prompt": codex_prompt,
        "reason": decision.get("reason"),
        "status": "PROPOSAL_CREATED",
        "lifecycle": [
            {
                "stage": "PROPOSAL_CREATED",
                "timestamp": now,
                "actor": "capability_evolution_evaluator",
                "note": f"Auto-generated by run {run_id} commit",
            },
            {
                "stage": "HUMAN_REVIEW_REQUIRED",
                "timestamp": now,
                "actor": "uipath_governance",
                "note": "Proposal awaits human review before any candidate generation or validation.",
            },
        ],
        "requires_human_approval": True,
        "coding_agent_allowed": "after_approval_only",
        "auto_execution_allowed": False,
        "rule_evaluation": decision.get("rule_evaluation"),
        "pattern_snapshot": decision.get("pattern_snapshot"),
        "why_not": decision.get("why_not"),
        "business_value": pattern.get("business_value"),
        "field_stability": pattern.get("field_stability"),
        "validation_pass_count": pattern.get("validation_pass_count"),
        "current_recommendation": pattern.get("current_recommendation"),
        "created_at": now,
        "updated_at": now,
    }
    _write_json(proposal_path(proposal_id), proposal)
    return proposal


def _validation_signal_from_run(run_id: str) -> dict[str, Any]:
    """Extract validation signal from raw artifacts (if any)."""
    from memory.run_memory import _load_artifact

    artifact = _load_artifact(run_id, "validation_response")
    data = artifact.get("data", {}) if isinstance(artifact, dict) else {}
    return {
        "validation_passed": (
            data.get("contract_test") == "passed"
            and data.get("business_rule_test") == "passed"
        )
        if data else None,
        "contract_test": data.get("contract_test"),
        "business_rule_test": data.get("business_rule_test"),
    }


@app.post(
    "/memory/runs/start",
    response_model=RunStartResponse,
    tags=["Run Memory"],
)
def memory_run_start(payload: RunStartRequest) -> RunStartResponse:
    """Start a new real run memory record.

    Creates ``memory/runs/{run_id}/`` with the canonical raw/normalized
    subdirectories, writes the ``CASE_RUN_STARTED`` event, and initializes
    ``cases/{case_id}/related_runs.json``.
    """
    from memory.run_memory import start_run

    result = start_run(
        case_id=payload.case_id,
        po_id=payload.po_id,
        workflow_name=payload.workflow_name,
        source=payload.source,
        demo_mode=payload.demo_mode,
    )
    return RunStartResponse(**result)


@app.post(
    "/memory/runs/{run_id}/events",
    response_model=RunEventResponse,
    tags=["Run Memory"],
)
def memory_run_event(run_id: str, payload: RunEventRequest) -> RunEventResponse:
    """Append a UiPath execution event to the run's raw event stream.

    Append-only — never overwrites prior events. Key event types also update
    ``normalized/case_timeline.json`` and ``cases/{case_id}/timeline.json``.
    """
    from memory.run_memory import append_event

    try:
        record = append_event(
            run_id,
            event_type=payload.event_type,
            case_id=payload.case_id,
            po_id=payload.po_id,
            stage=payload.stage,
            status=payload.status,
            payload=payload.payload,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return RunEventResponse(
        run_id=run_id,
        event_type=record["event_type"],
        occurred_at=record["occurred_at"],
        appended=True,
    )


@app.post(
    "/memory/runs/{run_id}/artifacts",
    response_model=RunArtifactResponse,
    tags=["Run Memory"],
)
def memory_run_artifact(
    run_id: str, payload: RunArtifactRequest
) -> RunArtifactResponse:
    """Persist a raw artifact for the run.

    ``http_call`` and ``error`` artifact types are appended to JSONL streams;
    other artifact types overwrite the latest file (always stamped with
    ``updated_at``).
    """
    from memory.run_memory import write_artifact

    try:
        result = write_artifact(
            run_id,
            artifact_type=payload.artifact_type,
            case_id=payload.case_id,
            data=payload.data,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return RunArtifactResponse(**result)


@app.post(
    "/memory/runs/{run_id}/complete",
    response_model=RunCompleteResponse,
    tags=["Run Memory"],
)
def memory_run_complete(
    run_id: str, payload: RunCompleteRequest
) -> RunCompleteResponse:
    """Mark the run as completed and update case-level state.

    Writes the ``RUN_COMPLETED`` event, updates
    ``normalized/case_state.json``, ``cases/{case_id}/case_state.json`` and
    ``cases/{case_id}/latest_run_id.txt``.
    """
    from memory.run_memory import complete_run

    try:
        record = complete_run(
            run_id,
            case_id=payload.case_id,
            result=payload.result,
            final_stage=payload.final_stage,
            execution_mode=payload.execution_mode,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return RunCompleteResponse(
        run_id=run_id,
        case_id=payload.case_id,
        status="RUN_COMPLETED",
        completed_at=record["occurred_at"],
    )


@app.post(
    "/memory/runs/{run_id}/commit",
    response_model=RunCommitResponse,
    tags=["Run Memory"],
)
def memory_run_commit(run_id: str) -> RunCommitResponse:
    """Commit a run: derive normalized memory, update patterns, run evolution.

    Steps:
      1. Build ``summary/case_run_summary.json`` from raw + normalized.
      2. Derive ``normalized/business_action.json``,
         ``normalized/side_effects_signature.json``,
         ``normalized/process_signature.json``.
      3. Incrementally update ``memory/patterns/{process_signature}.json``
         (returns before/after diff).
      4. Call the existing ``evaluate_capability_evolution`` evaluator using
         the derived business_action/exception_type.
      5. Write ``evolution/capability_evolution_decision.json`` and
         ``evolution/pattern_update.json``.
      6. If the decision is a proposal type, write
         ``memory/proposals/{proposal_id}.json``.
      7. Build ``summary/post_run_memory_summary.json``.

    Returns the run_id, process_signature, decision label, optional
    proposal_id and a dashboard URL.
    """
    from memory.run_memory import (
        _read_json,
        _write_json,
        _utc_iso,
        build_case_run_summary,
        build_post_run_memory_summary,
        derive_business_action,
        derive_exception_type,
        derive_process_signature,
        derive_policy_gate_family,
        derive_route_family,
        derive_side_effects_signature,
        derive_side_effects_family,
        run_dir,
        run_normalized_dir,
        run_summary_dir,
        run_evolution_dir,
    )
    from memory.patterns import increment_pattern

    if not run_dir(run_id).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run directory not found for run_id={run_id}",
        )

    state = _read_json(run_normalized_dir(run_id) / "case_state.json", {})
    case_id = state.get("case_id") or ""
    if not case_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run {run_id} has no case_id in normalized/case_state.json; "
            "call /memory/runs/{run_id}/complete first.",
        )

    # 1. Normalized derivations.
    business_action = derive_business_action(run_id, case_id=case_id)
    exception_type = derive_exception_type(run_id)
    side_effects = derive_side_effects_signature(run_id)
    route_family = derive_route_family(run_id)
    policy_gate_family = derive_policy_gate_family(run_id)
    side_effects_family = derive_side_effects_family(side_effects)
    process_signature = derive_process_signature(
        business_action,
        exception_type,
        route_family,
        policy_gate_family,
        side_effects,
    )

    normalized_dir = run_normalized_dir(run_id)
    _write_json(
        normalized_dir / "business_action.json",
        {
            "run_id": run_id,
            "case_id": case_id,
            "business_action": business_action,
            "derived_at": _utc_iso(),
        },
    )
    _write_json(
        normalized_dir / "side_effects_signature.json",
        {
            "run_id": run_id,
            "case_id": case_id,
            "side_effects_signature": side_effects,
            "derived_at": _utc_iso(),
        },
    )
    _write_json(
        normalized_dir / "process_signature.json",
        {
            "run_id": run_id,
            "case_id": case_id,
            "business_action": business_action,
            "exception_type": exception_type,
            "route_family": route_family,
            "policy_gate_family": policy_gate_family,
            "side_effects_family": side_effects_family,
            "process_signature": process_signature,
            "derived_at": _utc_iso(),
        },
    )

    # 2. Case run summary.
    case_run_summary = build_case_run_summary(run_id, case_id=case_id)
    _write_json(
        run_summary_dir(run_id) / "case_run_summary.json", case_run_summary
    )

    # 3. Incremental pattern update.
    validation_signal = _validation_signal_from_run(run_id)
    validation_passed = validation_signal.get("validation_passed")
    result = state.get("result")
    execution_mode = state.get("execution_mode")
    raw_dir = run_dir(run_id) / "raw"
    rpa_artifact = _read_json(raw_dir / "rpa_extracted_fields.json", {}) or {}
    route_artifact = _read_json(raw_dir / "route_plan.json", {}) or {}
    company_artifact = _read_json(raw_dir / "company_context_snapshot.json", {}) or {}
    agent_artifact = _read_json(raw_dir / "agent_reasoning_summary.json", {}) or {}
    rpa_data = rpa_artifact.get("data", rpa_artifact) if isinstance(rpa_artifact, dict) else {}
    route_data = route_artifact.get("data", route_artifact) if isinstance(route_artifact, dict) else {}
    company_data = (
        company_artifact.get("data", company_artifact)
        if isinstance(company_artifact, dict)
        else {}
    )
    agent_data = (
        agent_artifact.get("data", agent_artifact)
        if isinstance(agent_artifact, dict)
        else {}
    )
    pattern_update = increment_pattern(
        business_action=business_action,
        exception_type=exception_type,
        run_id=run_id,
        process_signature=process_signature,
        route_family=route_family,
        policy_gate_family=policy_gate_family,
        side_effects_family=side_effects_family,
        business_remarks=(
            rpa_data.get("business_remarks")
            or route_data.get("business_remarks")
            or ""
        ),
        company_context_used=(
            route_data.get("company_context_reference")
            or company_data.get("company_context_reference")
            or {}
        ),
        agent_reasoning_summary=(
            agent_data.get("agent_reasoning_summary")
            or route_data.get("agent_reasoning_summary")
            or ""
        ),
        result=result,
        execution_mode=execution_mode,
        validation_passed=validation_passed,
    )

    # 4. Capability evolution decision (reuse existing evaluator).
    evolution_request = CapabilityEvolutionEvaluateRequest(
        case_id=case_id,
        po_id=state.get("po_id"),
        exception_type=exception_type,
        business_action=business_action,
    )
    try:
        decision = evaluate_capability_evolution(evolution_request)
    except Exception as exc:  # pragma: no cover - defensive
        memory_logger.warning(
            "Run commit: capability evolution evaluation failed: %s", exc
        )
        decision = {
            "case_id": case_id,
            "decision": "KEEP_RPA_MODE",
            "reason": f"Capability evolution evaluation failed: {exc}",
            "requires_human_review": True,
        }

    # 5. Persist evolution decision + pattern update.
    evolution_dir = run_evolution_dir(run_id)
    decision_record = {
        "run_id": run_id,
        "case_id": case_id,
        "process_signature": process_signature,
        "evaluated_at": _utc_iso(),
        **decision,
    }
    _write_json(
        evolution_dir / "capability_evolution_decision.json",
        decision_record,
    )
    _write_json(evolution_dir / "pattern_update.json", pattern_update)

    # 6. Proposal lifecycle (only for proposal-type decisions).
    proposal_id: str | None = None
    decision_label = str(decision.get("decision", "KEEP_RPA_MODE"))
    if decision_label.endswith("_PROPOSAL") or "PROPOSAL" in decision_label:
        proposal_id = _next_proposal_id(_decision_kind(decision_label))
        _record_proposal(
            proposal_id=proposal_id,
            run_id=run_id,
            case_id=case_id,
            decision=decision,
            pattern=pattern_update.get("after", {}),
        )
        decision_record["proposal_id"] = proposal_id
        _write_json(
            evolution_dir / "capability_evolution_decision.json",
            decision_record,
        )

    # 7. Post-run memory summary.
    post_run_summary = build_post_run_memory_summary(
        run_id,
        case_id=case_id,
        case_run_summary=case_run_summary,
        pattern_update=pattern_update,
        evolution_decision=decision_record,
    )
    _write_json(
        run_summary_dir(run_id) / "post_run_memory_summary.json",
        post_run_summary,
    )

    dashboard_url = (
        f"http://localhost:8002/case-dashboard/{case_id}?run_id={run_id}"
    )

    return RunCommitResponse(
        run_id=run_id,
        case_id=case_id,
        process_signature=process_signature,
        case_run_summary_created=True,
        pattern_updated=bool(pattern_update.get("changed_fields")),
        capability_evolution_decision=decision_label,
        proposal_id=proposal_id,
        dashboard_url=dashboard_url,
    )


@app.get(
    "/memory/runs/{run_id}",
    tags=["Run Memory"],
)
def memory_run_view(run_id: str) -> dict[str, Any]:
    """Return the full inspectable structure for a run.

    Includes raw artifact list, normalized memory, summary, evolution
    decision, pattern update and proposal (when present).
    """
    from memory.run_memory import load_run_view

    try:
        return load_run_view(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
