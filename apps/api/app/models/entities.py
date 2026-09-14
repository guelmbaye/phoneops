"""Persistent mission state.

Architecture rule (DOCUMENT 03 §2): *no agent owns the truth, Mission State does*.
Every table below is written through the engine and read by every component, so
the causal chain call -> evidence -> constraint -> strategy -> next call is a
queryable fact rather than a narrative.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin
from app.domain import enums


class OperationalException(IdMixin, TimestampMixin, Base):
    """What broke."""

    __tablename__ = "operational_exceptions"

    type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_ref: Mapped[str] = mapped_column(String(128), nullable=False)  # e.g. Shipment #4821
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default=enums.Severity.CRITICAL)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Impact is first-class data (DOCUMENT 08 §9) - it is what recovery protects.
    threatened_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    consequence: Mapped[str] = mapped_column(Text, nullable=False)

    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual | tms | api
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    missions: Mapped[list[RecoveryMission]] = relationship(
        back_populates="exception", cascade="all, delete-orphan"
    )


class RecoveryMission(IdMixin, TimestampMixin, Base):
    __tablename__ = "recovery_missions"

    exception_id: Mapped[str] = mapped_column(
        ForeignKey("operational_exceptions.id", ondelete="CASCADE"), index=True
    )
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=enums.MissionStatus.ASSESSING, index=True
    )
    severity: Mapped[str] = mapped_column(String(16), default=enums.Severity.CRITICAL)

    recovery_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    current_strategy_id: Mapped[str | None] = mapped_column(String(64))
    replan_count: Mapped[int] = mapped_column(Integer, default=0)
    operation_count: Mapped[int] = mapped_column(Integer, default=0)

    autonomy_level: Mapped[int] = mapped_column(Integer, default=3)
    authority: Mapped[dict] = mapped_column(JSON, default=dict)  # allow/approval/prohibit lists
    limits: Mapped[dict] = mapped_column(JSON, default=dict)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)

    # many-to-one, eagerly loaded: every engine component needs the impact data.
    exception: Mapped[OperationalException] = relationship(
        back_populates="missions", lazy="selectin"
    )
    constraints: Mapped[list[MissionConstraint]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    strategies: Mapped[list[RecoveryStrategy]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    candidates: Mapped[list[RecoveryCandidate]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    information_needs: Mapped[list[InformationNeed]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    operations: Mapped[list[PhoneOperation]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    events: Mapped[list[MissionEvent]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )


class MissionConstraint(IdMixin, TimestampMixin, Base):
    """Hard boundaries. Evaluated deterministically - never by the LLM."""

    __tablename__ = "mission_constraints"

    mission_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_missions.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)  # pickup_time, capacity...
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    operator: Mapped[str] = mapped_column(String(8), nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), default=enums.ValueType.STRING)
    required_value: Mapped[dict] = mapped_column(JSON, nullable=False)  # {"value": ...}
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)

    mission: Mapped[RecoveryMission] = relationship(back_populates="constraints")

    __table_args__ = (UniqueConstraint("mission_id", "key", name="uq_constraint_mission_key"),)


class RecoveryCandidate(IdMixin, TimestampMixin, Base):
    """Reachable external actors. The replanner picks from here - not from thin air."""

    __tablename__ = "recovery_candidates"

    mission_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_missions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    region: Mapped[str] = mapped_column(String(8), default="US")
    locale: Mapped[str] = mapped_column(String(16), default="en-US")
    rank: Mapped[int] = mapped_column(Integer, default=100)
    eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    mission: Mapped[RecoveryMission] = relationship(back_populates="candidates")


class RecoveryStrategy(IdMixin, TimestampMixin, Base):
    """Versioned. A failed strategy is never overwritten (DOCUMENT 08 §12)."""

    __tablename__ = "recovery_strategies"

    mission_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_missions.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    type: Mapped[str] = mapped_column(String(64), default="replacement_carrier")
    target: Mapped[str | None] = mapped_column(String(128))
    target_candidate_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default=enums.StrategyStatus.PROPOSED)
    rationale: Mapped[str] = mapped_column(Text, default="")
    fingerprint: Mapped[str] = mapped_column(String(128), default="")
    information_needed: Mapped[list] = mapped_column(JSON, default=list)
    invalidated_reason: Mapped[str | None] = mapped_column(Text)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_by: Mapped[str] = mapped_column(String(24), default="deterministic")  # or llm

    mission: Mapped[RecoveryMission] = relationship(back_populates="strategies")
    attempts: Mapped[list[RecoveryAttempt]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan"
    )


class RecoveryAttempt(IdMixin, TimestampMixin, Base):
    __tablename__ = "recovery_attempts"

    mission_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_missions.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_strategies.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    target: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=enums.AttemptStatus.PENDING)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    strategy: Mapped[RecoveryStrategy] = relationship(back_populates="attempts")


class InformationNeed(IdMixin, TimestampMixin, Base):
    """The explicit "what we do not know yet" - the reason a call exists at all."""

    __tablename__ = "information_needs"

    mission_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_missions.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[str | None] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(128), nullable=False)  # who can answer
    description: Mapped[str] = mapped_column(Text, default="")
    required_for: Mapped[str] = mapped_column(String(128), default="")  # constraint key
    status: Mapped[str] = mapped_column(String(16), default=enums.InformationNeedStatus.UNKNOWN)

    mission: Mapped[RecoveryMission] = relationship(back_populates="information_needs")


class PhoneOperation(IdMixin, TimestampMixin, Base):
    """A bounded phone task handed to CALL-E."""

    __tablename__ = "phone_operations"

    mission_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_missions.id", ondelete="CASCADE"), index=True
    )
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    attempt_id: Mapped[str] = mapped_column(String(64), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    target: Mapped[str] = mapped_column(String(128), nullable=False)
    target_phone: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(
        String(48), default=enums.OperationPurpose.VERIFY_RECOVERY_FEASIBILITY
    )
    task: Mapped[str] = mapped_column(Text, nullable=False)  # CALL-E goal prompt
    requested_facts: Mapped[list] = mapped_column(JSON, default=list)
    questions: Mapped[list] = mapped_column(JSON, default=list)
    result_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=enums.OperationStatus.QUEUED)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(128), default="")
    # Why this call exists. Populated from the replan that produced it.
    triggered_by_evidence_id: Mapped[str | None] = mapped_column(String(64))
    triggered_by_replan_id: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    mission: Mapped[RecoveryMission] = relationship(back_populates="operations")
    # eager: the Mission Control view always renders an operation with its calls.
    calls: Mapped[list[CallRecord]] = relationship(
        back_populates="operation", cascade="all, delete-orphan", lazy="selectin"
    )


class CallRecord(IdMixin, TimestampMixin, Base):
    """Provenance anchor: the real CALL-E call id lives here."""

    __tablename__ = "call_records"

    mission_id: Mapped[str] = mapped_column(String(64), index=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("phone_operations.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(24), default="CALL-E")
    provider_mode: Mapped[str] = mapped_column(String(16), default="mock")
    call_id: Mapped[str] = mapped_column(String(128), index=True)
    display_ref: Mapped[str] = mapped_column(String(48), default="")  # "CALL-E #021"
    status: Mapped[str] = mapped_column(String(24), default="queued")
    outcome: Mapped[str | None] = mapped_column(String(24))
    structured_result: Mapped[dict] = mapped_column(JSON, default=dict)
    result_validation: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    transcript_excerpt: Mapped[str] = mapped_column(Text, default="")  # minimised on purpose
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    operation: Mapped[PhoneOperation] = relationship(back_populates="calls")

    __table_args__ = (Index("ix_call_records_call_id_unique", "call_id", unique=True),)


class Evidence(IdMixin, TimestampMixin, Base):
    """A structured fact with provenance. No mission-changing claim without one."""

    __tablename__ = "evidence"

    mission_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_missions.id", ondelete="CASCADE"), index=True
    )
    operation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    call_id: Mapped[str | None] = mapped_column(String(128), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(128), nullable=False)  # Carrier B
    type: Mapped[str] = mapped_column(String(64), nullable=False)  # pickup_time
    value: Mapped[dict] = mapped_column(JSON, nullable=False)  # {"value": "18:00"}
    value_type: Mapped[str] = mapped_column(String(16), default=enums.ValueType.STRING)
    raw_value: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(8), default=enums.Confidence.HIGH)
    status: Mapped[str] = mapped_column(String(16), default=enums.EvidenceStatus.DISCOVERED)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extractor: Mapped[str] = mapped_column(String(24), default="structured")
    notes: Mapped[str] = mapped_column(Text, default="")

    mission: Mapped[RecoveryMission] = relationship(back_populates="evidence")


class ConstraintEvaluation(IdMixin, TimestampMixin, Base):
    __tablename__ = "constraint_evaluations"

    mission_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    constraint_id: Mapped[str] = mapped_column(String(64), index=True)
    constraint_key: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(16), default=enums.ConstraintStatus.NOT_EVALUATED)
    required_value: Mapped[dict] = mapped_column(JSON, default=dict)
    observed_value: Mapped[dict] = mapped_column(JSON, default=dict)
    operator: Mapped[str] = mapped_column(String(8), default="eq")
    mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")


class Replan(IdMixin, TimestampMixin, Base):
    """The causal bridge: which call/evidence/constraint produced the next call."""

    __tablename__ = "replans"

    mission_id: Mapped[str] = mapped_column(String(64), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    trigger: Mapped[str] = mapped_column(
        String(48), default=enums.ReplanTrigger.CONSTRAINT_VIOLATED
    )
    previous_strategy_id: Mapped[str] = mapped_column(String(64))
    new_strategy_id: Mapped[str | None] = mapped_column(String(64))
    trigger_evidence_id: Mapped[str | None] = mapped_column(String(64))
    trigger_call_id: Mapped[str | None] = mapped_column(String(128))
    violated_constraint_id: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    required_value: Mapped[dict] = mapped_column(JSON, default=dict)
    observed_value: Mapped[dict] = mapped_column(JSON, default=dict)
    generated_by: Mapped[str] = mapped_column(String(24), default="deterministic")


class RecoveryDecision(IdMixin, TimestampMixin, Base):
    __tablename__ = "recovery_decisions"

    mission_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(32), default=enums.DecisionType.CONTINUE)
    actor: Mapped[str] = mapped_column(String(32), default=enums.Actor.DECISION_ENGINE)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    constraint_ids: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class PolicyDecision(IdMixin, TimestampMixin, Base):
    __tablename__ = "policy_decisions"

    mission_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(24), default=enums.PolicyOutcome.ALLOW)
    rule: Mapped[str] = mapped_column(String(96), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ApprovalRequest(IdMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"

    mission_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(96))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment: Mapped[str] = mapped_column(Text, default="")


class RecoveryOutcome(IdMixin, TimestampMixin, Base):
    __tablename__ = "recovery_outcomes"

    mission_id: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    status: Mapped[str] = mapped_column(String(16), default=enums.OutcomeStatus.RECOVERED)
    selected_strategy_id: Mapped[str | None] = mapped_column(String(64))
    selected_target: Mapped[str | None] = mapped_column(String(128))
    protected_outcome: Mapped[str] = mapped_column(Text, default="")
    headline: Mapped[str] = mapped_column(String(160), default="")
    margin_seconds: Mapped[int | None] = mapped_column(Integer)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MissionEvent(IdMixin, Base):
    """Append-only audit trail. Also the Mission Control timeline and the SSE feed."""

    __tablename__ = "mission_events"

    mission_id: Mapped[str] = mapped_column(
        ForeignKey("recovery_missions.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(32), default=enums.Actor.PHONEOPS)
    strategy_id: Mapped[str | None] = mapped_column(String(64))
    attempt_id: Mapped[str | None] = mapped_column(String(64))
    operation_id: Mapped[str | None] = mapped_column(String(64))
    call_id: Mapped[str | None] = mapped_column(String(128))
    evidence_id: Mapped[str | None] = mapped_column(String(64))
    constraint_id: Mapped[str | None] = mapped_column(String(64))
    replan_id: Mapped[str | None] = mapped_column(String(64))
    strategic: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str] = mapped_column(String(160), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    mission: Mapped[RecoveryMission] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_mission_events_mission_seq", "mission_id", "sequence"),
        UniqueConstraint("mission_id", "sequence", name="uq_event_mission_sequence"),
    )
