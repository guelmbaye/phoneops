"""Public API contracts (request/response DTOs).

The frontend is a projection of backend truth (DOCUMENT 08 §40): the UI never
recomputes whether a plan is still valid - it renders what the engine decided.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.domain import enums


def _stamp_utc(value: Any) -> Any:
    """Guarantee every datetime leaves the API with an explicit offset.

    SQLite has no timezone type, so instants come back from the database naive.
    Serialised without an offset, `new Date("2026-09-06T10:29:05")` is parsed by
    the browser as *local* time: the timeline then renders an hour off from the
    cutoff, which is the one field that happened to be re-stamped by hand.
    """
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


#: Use instead of `datetime` in every response schema.
UtcDatetime = Annotated[datetime, BeforeValidator(_stamp_utc)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ requests
class ConstraintInput(BaseModel):
    key: str
    label: str | None = None
    operator: enums.ConstraintOperator = enums.ConstraintOperator.LTE
    value_type: enums.ValueType = enums.ValueType.STRING
    required_value: Any
    mandatory: bool = True


class CandidateInput(BaseModel):
    name: str
    phone: str
    region: str = "US"
    locale: str = "en-US"
    rank: int = 100
    eligible: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)


class ExceptionCreate(BaseModel):
    type: enums.ExceptionType = enums.ExceptionType.CARRIER_CANCELLATION
    entity_ref: str = Field(..., examples=["Shipment #4821"])
    description: str
    severity: enums.Severity = enums.Severity.CRITICAL
    threatened_outcome: str = Field(..., examples=["Tonight's shipment departure"])
    consequence: str = Field(..., examples=["Shipment may miss tonight's departure"])
    recovery_cutoff: UtcDatetime | None = None
    cutoff_clock: str | None = Field(
        default=None, description="'17:30' - resolved against today when recovery_cutoff is unset"
    )
    detected_at: UtcDatetime | None = None
    source: str = "manual"
    constraints: list[ConstraintInput] = Field(default_factory=list)
    candidates: list[CandidateInput] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    autostart: bool = False
    demo: bool = False


class MissionStartRequest(BaseModel):
    force: bool = False


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    decided_by: str = "operator"
    comment: str = ""


class CallResultIngest(BaseModel):
    """Manual/simulated ingestion - used by tests and the demo harness."""

    call_id: str | None = None
    outcome: enums.CallOutcome = enums.CallOutcome.COMPLETED
    structured_result: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    transcript_excerpt: str = ""


class CalleWebhookPayload(BaseModel):
    """Terminal call result webhook (POST {API_PREFIX}/webhooks/calle)."""

    model_config = ConfigDict(extra="allow")

    call_id: str | None = None
    id: str | None = None
    event: str | None = None
    type: str | None = None
    status: str | None = None
    structured_result: dict[str, Any] | None = None
    structuredResult: dict[str, Any] | None = None
    result_validation: dict[str, Any] | None = None
    summary: str | None = None
    transcript: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def resolved_call_id(self) -> str | None:
        return self.call_id or self.id or self.metadata.get("call_id")

    def resolved_result(self) -> dict[str, Any]:
        return self.structured_result or self.structuredResult or {}


# ----------------------------------------------------------------- responses
class ExceptionOut(ORMModel):
    id: str
    type: str
    entity_ref: str
    description: str
    severity: str
    detected_at: UtcDatetime
    threatened_outcome: str
    consequence: str
    source: str


class ConstraintOut(ORMModel):
    id: str
    key: str
    label: str
    #: Short noun phrase ("the pickup cutoff") for sentences. Served from the
    #: backend so the wording cannot drift between the timeline and the UI.
    short_label: str = ""
    operator: str
    value_type: str
    required_value: dict[str, Any]
    mandatory: bool


class CandidateOut(ORMModel):
    id: str
    name: str
    phone: str
    region: str
    locale: str
    rank: int
    eligible: bool
    attempted: bool
    exhausted: bool


class StrategyOut(ORMModel):
    id: str
    version: int
    type: str
    target: str | None
    status: str
    rationale: str
    information_needed: list[Any]
    invalidated_reason: str | None
    invalidated_at: UtcDatetime | None
    generated_by: str
    created_at: UtcDatetime


class InformationNeedOut(ORMModel):
    id: str
    key: str
    subject: str
    description: str
    required_for: str
    status: str


class CallOut(ORMModel):
    id: str
    call_id: str
    display_ref: str
    provider: str
    provider_mode: str
    status: str
    outcome: str | None
    summary: str
    duration_seconds: float | None
    started_at: UtcDatetime | None
    ended_at: UtcDatetime | None


class OperationOut(ORMModel):
    id: str
    sequence: int
    target: str
    target_phone: str
    purpose: str
    status: str
    attempt_number: int
    requested_facts: list[Any]
    questions: list[Any]
    reason: str
    triggered_by_evidence_id: str | None
    triggered_by_replan_id: str | None
    strategy_id: str
    requested_at: UtcDatetime | None
    completed_at: UtcDatetime | None
    calls: list[CallOut] = Field(default_factory=list)


class EvidenceOut(ORMModel):
    id: str
    subject: str
    type: str
    value: dict[str, Any]
    value_type: str
    raw_value: str
    confidence: str
    status: str
    call_id: str | None
    operation_id: str | None
    strategy_id: str | None
    observed_at: UtcDatetime
    valid_until: UtcDatetime | None
    extractor: str
    notes: str


class ConstraintEvaluationOut(ORMModel):
    id: str
    constraint_id: str
    constraint_key: str
    subject: str
    status: str
    required_value: dict[str, Any]
    observed_value: dict[str, Any]
    operator: str
    mandatory: bool
    evidence_ids: list[Any]
    explanation: str
    strategy_id: str
    created_at: UtcDatetime


class ReplanOut(ORMModel):
    id: str
    sequence: int
    trigger: str
    previous_strategy_id: str
    new_strategy_id: str | None
    trigger_evidence_id: str | None
    trigger_call_id: str | None
    violated_constraint_id: str | None
    reason: str
    required_value: dict[str, Any]
    observed_value: dict[str, Any]
    generated_by: str
    created_at: UtcDatetime


class ApprovalOut(ORMModel):
    id: str
    action: str
    reason: str
    payload: dict[str, Any]
    status: str
    decided_by: str | None
    decided_at: UtcDatetime | None


class OutcomeOut(ORMModel):
    id: str
    status: str
    selected_target: str | None
    protected_outcome: str
    headline: str
    margin_seconds: int | None
    facts: dict[str, Any]
    evidence_ids: list[Any]
    metrics: dict[str, Any]
    completed_at: UtcDatetime


class EventOut(ORMModel):
    id: str
    sequence: int
    type: str
    actor: str
    title: str
    strategic: bool
    strategy_id: str | None
    operation_id: str | None
    call_id: str | None
    evidence_id: str | None
    constraint_id: str | None
    replan_id: str | None
    payload: dict[str, Any]
    created_at: UtcDatetime


class DeadlineOut(BaseModel):
    cutoff_at: UtcDatetime
    remaining_seconds: int
    expired: bool
    #: IANA zone the mission's wall-clock times belong to. The UI must format
    #: every mission timestamp in it, or the cutoff will contradict the
    #: constraint it is derived from.
    timezone: str


class MissionSummary(BaseModel):
    id: str
    status: str
    objective: str
    severity: str
    entity_ref: str
    exception_type: str
    deadline: DeadlineOut
    current_strategy_target: str | None
    replan_count: int
    operation_count: int
    created_at: UtcDatetime


class StrategyDiffOut(BaseModel):
    """Powers the hero UX moment (DOCUMENT 04 §19-20)."""

    before: StrategyOut | None = None
    after: StrategyOut | None = None
    reason: str = ""
    required_value: dict[str, Any] = Field(default_factory=dict)
    observed_value: dict[str, Any] = Field(default_factory=dict)
    trigger_call_id: str | None = None
    trigger_evidence_id: str | None = None


class MissionMetrics(BaseModel):
    #: Detection -> confirmed recovery. What the operation was exposed to.
    phone_operations: int
    strategies_evaluated: int
    automatic_replans: int
    human_interventions: int
    evidence_count: int
    time_to_recovery_seconds: int | None
    #: Start -> completion. Engine execution only; kept for regression tracking
    #: and deliberately absent from Mission Control.
    engine_seconds: int | None = None


class RecoveryView(BaseModel):
    """Single aggregate the Mission Control screen renders."""

    mission: MissionSummary
    exception: ExceptionOut
    impact: dict[str, Any]
    constraints: list[ConstraintOut]
    information_needs: list[InformationNeedOut]
    current_strategy: StrategyOut | None
    strategies: list[StrategyOut]
    active_operation: OperationOut | None
    operations: list[OperationOut]
    evidence: list[EvidenceOut]
    constraint_evaluations: list[ConstraintEvaluationOut]
    latest_replan: ReplanOut | None
    strategy_diff: StrategyDiffOut | None
    pending_approval: ApprovalOut | None
    outcome: OutcomeOut | None
    #: Canonical wording for every fact key in this mission, keyed by key. The
    #: UI renders these rather than deriving its own, so a fact cannot be called
    #: "availability" in the timeline and "Available" on the card beside it.
    fact_labels: dict[str, str] = Field(default_factory=dict)
    timeline: list[EventOut]
    metrics: MissionMetrics
    candidates: list[CandidateOut]


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    app: str
    version: str
    env: str
    calle_mode: str
    calle_configured: bool
    llm_enabled: bool
    database: str
    event_bus: str
