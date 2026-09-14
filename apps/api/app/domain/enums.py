"""Vocabulary of the recovery domain.

Naming follows DOCUMENT 04 §39 (Semantic Status Language) so the API, the audit
trail and the Mission Control UI all speak exactly the same language.
"""

from __future__ import annotations

from enum import StrEnum


class ExceptionType(StrEnum):
    CARRIER_CANCELLATION = "carrier_cancellation"
    MISSED_PICKUP = "missed_pickup"
    WAREHOUSE_CLOSURE = "warehouse_closure"
    SUPPLIER_CAPACITY_LOSS = "supplier_capacity_loss"
    TECHNICIAN_UNAVAILABLE = "technician_unavailable"
    SERVICE_DISPATCH_FAILURE = "service_dispatch_failure"
    OTHER = "other"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class MissionStatus(StrEnum):
    ASSESSING = "assessing"
    PLANNING = "planning"
    EXECUTING = "executing"
    LEARNING = "learning"
    EVALUATING = "evaluating"
    REPLANNING = "replanning"
    APPROVAL_REQUIRED = "approval_required"
    RECOVERED = "recovered"
    ESCALATED = "escalated"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {MissionStatus.RECOVERED, MissionStatus.ESCALATED, MissionStatus.FAILED}


class StrategyStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    INVALIDATED = "invalidated"
    SUCCESSFUL = "successful"
    ABANDONED = "abandoned"


class AttemptStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INVALIDATED = "invalidated"


class OperationStatus(StrEnum):
    QUEUED = "queued"
    CALLING = "calling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationPurpose(StrEnum):
    VERIFY_RECOVERY_FEASIBILITY = "verify_recovery_feasibility"
    CLARIFICATION = "clarification"
    VERIFICATION = "verification"
    RETRY = "retry"


class CallOutcome(StrEnum):
    COMPLETED = "completed"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    VOICEMAIL = "voicemail"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvidenceStatus(StrEnum):
    """A fact only influences a hard constraint when VALIDATED."""

    DISCOVERED = "discovered"
    VALIDATED = "validated"
    UNCERTAIN = "uncertain"
    CONFLICTING = "conflicting"
    STALE = "stale"
    REJECTED = "rejected"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ConstraintOperator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    LTE = "lte"
    GTE = "gte"
    LT = "lt"
    GT = "gt"
    IN = "in"


class ValueType(StrEnum):
    TIME = "time"
    DATETIME = "datetime"
    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"


class ConstraintStatus(StrEnum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNCERTAIN = "uncertain"
    NOT_EVALUATED = "not_evaluated"


class InformationNeedStatus(StrEnum):
    UNKNOWN = "unknown"
    REQUESTED = "requested"
    RESOLVED = "resolved"


class DecisionType(StrEnum):
    CONTINUE = "continue"
    CLARIFY = "clarify"
    REPLAN = "replan"
    RECOVERED = "recovered"
    APPROVAL_REQUIRED = "approval_required"
    ESCALATE = "escalate"


class PolicyOutcome(StrEnum):
    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"
    BLOCK = "block"


class ReplanTrigger(StrEnum):
    CONSTRAINT_VIOLATED = "constraint_violated"
    TARGET_UNAVAILABLE = "target_unavailable"
    CALL_FAILURE = "call_failure"
    ASSUMPTION_INVALIDATED = "assumption_invalidated"
    TIME_WINDOW_CHANGED = "time_window_changed"
    CRITICAL_EVIDENCE_CHANGED = "critical_evidence_changed"


class OutcomeStatus(StrEnum):
    RECOVERED = "recovered"
    ESCALATED = "escalated"
    FAILED = "failed"


class Actor(StrEnum):
    USER = "user"
    PHONEOPS = "phoneops"
    CALL_E = "call-e"
    EXTERNAL_ACTOR = "external_actor"
    CONSTRAINT_ENGINE = "constraint_engine"
    POLICY_ENGINE = "policy_engine"
    REPLANNER = "replanner"
    PLANNER = "planner"
    DECISION_ENGINE = "decision_engine"


class EventType(StrEnum):
    EXCEPTION_DETECTED = "EXCEPTION_DETECTED"
    RECOVERY_MISSION_CREATED = "RECOVERY_MISSION_CREATED"
    RECOVERY_STARTED = "RECOVERY_STARTED"
    RECOVERY_PLAN_CREATED = "RECOVERY_PLAN_CREATED"
    INFORMATION_GAP_IDENTIFIED = "INFORMATION_GAP_IDENTIFIED"
    RECOVERY_ATTEMPT_STARTED = "RECOVERY_ATTEMPT_STARTED"
    CALL_REQUESTED = "CALL_REQUESTED"
    CALL_STARTED = "CALL_STARTED"
    CALL_COMPLETED = "CALL_COMPLETED"
    CALL_FAILED = "CALL_FAILED"
    EVIDENCE_DISCOVERED = "EVIDENCE_DISCOVERED"
    EVIDENCE_UNCERTAIN = "EVIDENCE_UNCERTAIN"
    EVIDENCE_CONFLICTING = "EVIDENCE_CONFLICTING"
    CONSTRAINT_EVALUATED = "CONSTRAINT_EVALUATED"
    CONSTRAINT_VIOLATED = "CONSTRAINT_VIOLATED"
    STRATEGY_INVALIDATED = "STRATEGY_INVALIDATED"
    REPLAN_STARTED = "REPLAN_STARTED"
    RECOVERY_REPLANNED = "RECOVERY_REPLANNED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    RECOVERY_ATTEMPT_COMPLETED = "RECOVERY_ATTEMPT_COMPLETED"
    RECOVERY_PATH_CONFIRMED = "RECOVERY_PATH_CONFIRMED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    RECOVERY_COMPLETED = "RECOVERY_COMPLETED"
    RECOVERY_ESCALATED = "RECOVERY_ESCALATED"
    MISSION_HEARTBEAT = "MISSION_HEARTBEAT"


#: Events the Mission Control timeline shows (DOCUMENT 04 §30: no noisy internals).
STRATEGIC_EVENTS: frozenset[str] = frozenset(
    {
        EventType.EXCEPTION_DETECTED,
        EventType.RECOVERY_STARTED,
        EventType.RECOVERY_PLAN_CREATED,
        EventType.CALL_REQUESTED,
        EventType.CALL_COMPLETED,
        EventType.CALL_FAILED,
        EventType.EVIDENCE_DISCOVERED,
        EventType.CONSTRAINT_VIOLATED,
        EventType.STRATEGY_INVALIDATED,
        EventType.RECOVERY_REPLANNED,
        EventType.RECOVERY_PATH_CONFIRMED,
        EventType.APPROVAL_REQUIRED,
        EventType.RECOVERY_COMPLETED,
        EventType.RECOVERY_ESCALATED,
    }
)
