"""Recovery Director - owner of the mission lifecycle.

This is the closed adaptive loop:

    EXCEPTION -> OBJECTIVE -> INFORMATION GAP -> CALL-E -> EVIDENCE
      -> CONSTRAINT CHECK -> CONTINUE / INVALIDATE -> REPLAN -> CALL-E AGAIN
      -> RECOVER / ESCALATE

The Director coordinates; it never improvises a call, never overwrites evidence
and never declares recovery without validated constraints.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import new_id
from app.db.session import session_scope
from app.domain.enums import (
    Actor,
    AttemptStatus,
    ConstraintStatus,
    DecisionType,
    EventType,
    EvidenceStatus,
    MissionStatus,
    OperationPurpose,
    OperationStatus,
    OutcomeStatus,
    PolicyOutcome,
    ReplanTrigger,
    StrategyStatus,
)
from app.domain.errors import CalleError, ConflictError, NotFoundError
from app.engine import call_operations, constraints, decision, guardrails, planner, policy
from app.engine.intelligence import extract
from app.events.recorder import record_event
from app.integrations.calle.base import CallState
from app.logging_config import get_logger
from app.models import (
    ApprovalRequest,
    CallRecord,
    Evidence,
    PhoneOperation,
    RecoveryAttempt,
    RecoveryDecision,
    RecoveryMission,
    RecoveryOutcome,
    RecoveryStrategy,
    Replan,
)
from app.services.catalog import get_template
from app.services.timeutil import (
    as_aware,
    parse_time,
    remaining_seconds,
    utcnow,
    wall_clock,
)
from app.services.wording import evaluation_title, evidence_title

log = get_logger("engine.director")

#: Per-mission serialisation: two call results must never replan concurrently.
_locks: dict[str, asyncio.Lock] = {}

#: Failure codes where the number itself is the problem. A retry is wasted money.
_PERMANENT_FAILURES = ("invalid_number", "unallocated", "blocked", "do_not_call", "unsupported")


def mission_lock(mission_id: str) -> asyncio.Lock:
    lock = _locks.get(mission_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[mission_id] = lock
    return lock


def cutoff_clock(mission: RecoveryMission) -> str:
    return wall_clock(mission.recovery_cutoff)


# ---------------------------------------------------------------------------
# 1. Mission start: objective -> strategy -> information gap -> first call
# ---------------------------------------------------------------------------
async def prepare_mission(
    session: AsyncSession, mission: RecoveryMission
) -> RecoveryStrategy | None:
    """Plan the first strategy and name what is still unknown — before calling.

    Run at intake, so an operator opening the mission sees the objective and the
    open questions before any phone call exists. That state is the product's own
    argument: the recovery is blocked on information no connected system holds,
    and only a call can supply it. Registering it at start instead meant the
    brief read "None outstanding" until CALL-E was already dialling.

    Returns None when the mission was escalated for lack of a candidate.
    """
    existing = (
        (
            await session.execute(
                select(RecoveryStrategy)
                .where(RecoveryStrategy.mission_id == mission.id)
                .order_by(RecoveryStrategy.version.asc())
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing

    exception_type = mission.exception.type
    proposal = await planner.plan_initial(session, mission, exception_type=exception_type)
    if not proposal.target:
        await _escalate(session, mission, proposal.no_candidate_reason, rule="no_candidate")
        return None

    template = get_template(exception_type)
    strategy = await planner.create_strategy(
        session, mission, proposal, strategy_type=template.strategy_type, version=1
    )
    needs = await planner.register_information_needs(
        session, mission, strategy, exception_type=exception_type
    )

    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.RECOVERY_PLAN_CREATED,
        title=f"Recovery strategy 01 — {strategy.target}",
        strategy_id=strategy.id,
        payload={
            "target": strategy.target,
            "rationale": strategy.rationale,
            "information_needed": strategy.information_needed,
            "generated_by": strategy.generated_by,
        },
    )
    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.INFORMATION_GAP_IDENTIFIED,
        title="Recovery blocked by missing information",
        strategy_id=strategy.id,
        actor=Actor.PLANNER,
        payload={
            "unknown": [
                {"key": n.key, "subject": n.subject, "description": n.description} for n in needs
            ],
            "next_action": "CALL-E",
        },
    )
    return strategy


async def start_mission(session: AsyncSession, mission_id: str) -> RecoveryMission:
    mission = await _load(session, mission_id)
    if (
        mission.status.is_terminal
        if isinstance(mission.status, MissionStatus)
        else MissionStatus(mission.status).is_terminal
    ):
        raise ConflictError(f"Mission {mission_id} already finished ({mission.status}).")
    if mission.started_at is not None:
        raise ConflictError(f"Mission {mission_id} is already running.")

    deadline = guardrails.check_deadline(mission)
    if not deadline.ok:
        await _escalate(session, mission, deadline.reason, rule=deadline.rule)
        return mission

    # Atomic claim. Reading `started_at` above and writing it here are two
    # separate awaits, so a second caller (a double-clicked START RECOVERY, or a
    # demo autostart racing an explicit start) can pass the same check. The
    # conditional UPDATE lets exactly one caller win, and a mission that ran
    # twice would emit a duplicated timeline - the one artefact that would make
    # the audit trail untrustworthy.
    claim = await session.execute(
        update(RecoveryMission)
        .where(RecoveryMission.id == mission_id, RecoveryMission.started_at.is_(None))
        .values(started_at=utcnow(), status=MissionStatus.PLANNING)
    )
    if claim.rowcount == 0:
        raise ConflictError(f"Mission {mission_id} is already running.")
    await session.refresh(mission)
    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.RECOVERY_STARTED,
        title="Recovery started",
        payload={"objective": mission.objective, "cutoff": cutoff_clock(mission)},
    )

    strategy = await prepare_mission(session, mission)
    if strategy is None:
        return mission

    await _launch_operation(
        session,
        mission=mission,
        strategy=strategy,
        facts=list(strategy.information_needed or []),
        reason="Initial recovery hypothesis requires external confirmation.",
    )
    return mission


# ---------------------------------------------------------------------------
# 2. Operation launch: attempt -> phone operation -> policy -> CALL-E
# ---------------------------------------------------------------------------
async def _launch_operation(
    session: AsyncSession,
    *,
    mission: RecoveryMission,
    strategy: RecoveryStrategy,
    facts: list[str],
    reason: str,
    purpose: OperationPurpose = OperationPurpose.VERIFY_RECOVERY_FEASIBILITY,
    triggered_by_evidence_id: str | None = None,
    triggered_by_replan_id: str | None = None,
    attempt_number: int = 1,
    clarify_fact: str | None = None,
    attempt: RecoveryAttempt | None = None,
) -> PhoneOperation | None:
    budget = guardrails.check_operation_budget(mission)
    if not budget.ok:
        await _escalate(session, mission, budget.reason, rule=budget.rule)
        return None

    gate = await policy.enforce(
        session, mission, "call_candidate", context={"target": strategy.target}
    )
    if gate.outcome is PolicyOutcome.BLOCK:
        await record_event(
            session,
            mission_id=mission.id,
            type=EventType.POLICY_BLOCKED,
            title="Phone operation blocked by policy",
            actor=Actor.POLICY_ENGINE,
            strategy_id=strategy.id,
            payload={"reason": gate.reason},
        )
        await _escalate(session, mission, gate.reason, rule=gate.rule)
        return None

    if attempt is None:
        attempt = RecoveryAttempt(
            id=new_id("attempt"),
            mission_id=mission.id,
            strategy_id=strategy.id,
            sequence=await _attempt_sequence(session, strategy.id),
            target=strategy.target or "unknown",
            status=AttemptStatus.RUNNING,
            started_at=utcnow(),
        )
        session.add(attempt)
        await session.flush()
        await record_event(
            session,
            mission_id=mission.id,
            type=EventType.RECOVERY_ATTEMPT_STARTED,
            title=f"Recovery attempt — {attempt.target}",
            strategy_id=strategy.id,
            attempt_id=attempt.id,
        )

    operation = await call_operations.create_operation(
        session,
        mission=mission,
        strategy=strategy,
        attempt=attempt,
        entity_ref=mission.exception.entity_ref,
        cutoff_clock=cutoff_clock(mission),
        facts=facts,
        purpose=purpose,
        reason=reason,
        triggered_by_evidence_id=triggered_by_evidence_id,
        triggered_by_replan_id=triggered_by_replan_id,
        attempt_number=attempt_number,
        clarify_fact=clarify_fact,
    )

    mission.status = MissionStatus.EXECUTING
    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.CALL_REQUESTED,
        title=f"CALL-E calls {operation.target}",
        strategy_id=strategy.id,
        attempt_id=attempt.id,
        operation_id=operation.id,
        payload={
            "target": operation.target,
            "purpose": operation.purpose,
            "requested_facts": operation.requested_facts,
            "reason": operation.reason,
            "triggered_by_evidence_id": operation.triggered_by_evidence_id,
            "triggered_by_replan_id": operation.triggered_by_replan_id,
        },
    )

    try:
        record, state = await call_operations.dispatch(
            session, mission=mission, operation=operation
        )
    except CalleError as exc:
        operation.status = OperationStatus.FAILED
        # A provider that refused the request is not a carrier who did not
        # answer. Every call returning 422 escalated as "no candidate could be
        # reached" — a plausible business story hiding an integration fault,
        # with no phone ever dialled. Say which it was.
        rejected = bool(exc.details.get("rejected"))
        await record_event(
            session,
            mission_id=mission.id,
            type=EventType.CALL_FAILED,
            title=(
                f"CALL-E refused the request for {operation.target} — no call was placed"
                if rejected
                else f"CALL-E could not reach {operation.target}"
            ),
            actor=Actor.CALL_E,
            strategy_id=strategy.id,
            operation_id=operation.id,
            payload={"error": exc.message, "transient": exc.transient, "rejected": rejected},
        )
        if rejected:
            # Retrying an identical payload cannot help, and moving to the next
            # carrier would burn the whole candidate list on our own bug.
            await _escalate(
                session,
                mission,
                f"CALL-E rejected the call request: {exc.message}",
                rule="provider_rejected_request",
            )
            return operation
        await _handle_call_failure(session, mission=mission, operation=operation, error=exc.message)
        return operation

    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.CALL_STARTED,
        title=f"{record.display_ref} — {operation.target}",
        actor=Actor.CALL_E,
        strategy_id=strategy.id,
        operation_id=operation.id,
        call_id=record.call_id,
        payload={"mode": record.provider_mode, "display_ref": record.display_ref},
    )

    if state.is_terminal:
        await ingest_call_state(
            session, mission=mission, operation=operation, record=record, state=state
        )
    return operation


# ---------------------------------------------------------------------------
# 3. Call result ingestion -> evidence -> constraints -> decision
# ---------------------------------------------------------------------------
async def ingest_call_state(
    session: AsyncSession,
    *,
    mission: RecoveryMission,
    operation: PhoneOperation,
    record: CallRecord,
    state: CallState,
) -> None:
    # Idempotency: a webhook may arrive twice, or race with the poller.
    if operation.status == OperationStatus.COMPLETED:
        log.info("call.ingest.duplicate", operation_id=operation.id, call_id=record.call_id)
        return

    call_operations.apply_state(record, state)
    mission.status = MissionStatus.LEARNING
    await session.flush()

    if call_operations.is_business_failure(state):
        operation.status = OperationStatus.FAILED
        operation.completed_at = utcnow()
        await record_event(
            session,
            mission_id=mission.id,
            type=EventType.CALL_FAILED,
            # The provider's own reason, on screen. "failed" alone sent an
            # operator back to the server log, which is where the useful half
            # of every diagnosis has been hiding all along.
            title=(
                f"{record.display_ref} — {str(state.outcome).replace('_', ' ')}"
                + (f" ({state.error})" if state.error else "")
            ),
            actor=Actor.CALL_E,
            strategy_id=operation.strategy_id,
            operation_id=operation.id,
            call_id=record.call_id,
            payload={"outcome": state.outcome, "error": state.error},
        )
        log.warning(
            "call.failed",
            call_id=record.call_id,
            target=operation.target,
            outcome=str(state.outcome),
            reason=state.error or "",
        )
        await _handle_call_failure(
            session, mission=mission, operation=operation, error=str(state.outcome)
        )
        return

    operation.status = OperationStatus.COMPLETED
    operation.completed_at = utcnow()
    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.CALL_COMPLETED,
        title=f"{record.display_ref} completed",
        actor=Actor.CALL_E,
        strategy_id=operation.strategy_id,
        operation_id=operation.id,
        call_id=record.call_id,
        payload={"summary": record.summary, "duration_seconds": record.duration_seconds},
    )

    evidence = await extract(
        session, mission=mission, operation=operation, call_record=record, state=state
    )
    for item in evidence:
        event_type = {
            EvidenceStatus.VALIDATED: EventType.EVIDENCE_DISCOVERED,
            EvidenceStatus.CONFLICTING: EventType.EVIDENCE_CONFLICTING,
        }.get(item.status, EventType.EVIDENCE_UNCERTAIN)
        await record_event(
            session,
            mission_id=mission.id,
            type=event_type,
            title=evidence_title(item.subject, item.type, item.value.get("value")),
            actor=Actor.EXTERNAL_ACTOR,
            strategy_id=operation.strategy_id,
            operation_id=operation.id,
            call_id=record.call_id,
            evidence_id=item.id,
            payload={
                "subject": item.subject,
                "type": item.type,
                "value": item.value.get("value"),
                "confidence": item.confidence,
                "status": item.status,
                "raw": item.raw_value,
                "source": record.display_ref,
            },
        )

    await planner.resolve_information_needs(
        session,
        strategy_id=operation.strategy_id,
        resolved_keys={e.type for e in evidence if e.status == EvidenceStatus.VALIDATED},
    )
    await _evaluate_and_decide(session, mission=mission, operation=operation, record=record)


# ---------------------------------------------------------------------------
# 4. Evaluation + decision
# ---------------------------------------------------------------------------
async def _evaluate_and_decide(
    session: AsyncSession,
    *,
    mission: RecoveryMission,
    operation: PhoneOperation,
    record: CallRecord,
) -> None:
    strategy = await session.get(RecoveryStrategy, operation.strategy_id)
    mission.status = MissionStatus.EVALUATING

    results = await constraints.evaluate_mission(
        session, mission, subject=operation.target, strategy_id=strategy.id
    )
    for result in results:
        if result.status is ConstraintStatus.NOT_EVALUATED:
            continue
        event_type = (
            EventType.CONSTRAINT_VIOLATED
            if result.status is ConstraintStatus.VIOLATED
            else EventType.CONSTRAINT_EVALUATED
        )
        await record_event(
            session,
            mission_id=mission.id,
            type=event_type,
            # The precise wording survives on ConstraintEvaluation.explanation
            # and in this event's payload; the title is what a human reads.
            title=evaluation_title(
                operation.target,
                key=result.constraint.key,
                operator=result.constraint.operator,
                required=result.constraint.required_value.get("value"),
                observed=result.observed_value,
                satisfied=result.status is ConstraintStatus.SATISFIED,
            ),
            actor=Actor.CONSTRAINT_ENGINE,
            strategy_id=strategy.id,
            operation_id=operation.id,
            call_id=record.call_id,
            constraint_id=result.constraint.id,
            evidence_id=result.evidence.id if result.evidence else None,
            payload={
                "constraint": result.constraint.key,
                "operator": result.constraint.operator,
                "required": result.constraint.required_value.get("value"),
                "observed": result.observed_value,
                "status": result.status,
                "mandatory": result.constraint.mandatory,
            },
        )

    clarifications = await _clarification_count(session, mission.id, operation.target)
    verdict = decision.decide(
        mission,
        results,
        clarification_budget_left=clarifications < settings.MAX_CLARIFICATIONS_PER_TARGET,
    )
    session.add(
        RecoveryDecision(
            id=new_id("dec"),
            mission_id=mission.id,
            strategy_id=strategy.id,
            type=verdict.type,
            reason=verdict.reason,
            evidence_ids=verdict.evidence_ids,
            constraint_ids=verdict.constraint_ids,
            payload=verdict.payload,
        )
    )
    await session.flush()

    if verdict.type is DecisionType.RECOVERED:
        await _confirm_recovery(
            session, mission=mission, strategy=strategy, results=results, verdict=verdict
        )
    elif verdict.type is DecisionType.CLARIFY:
        await _launch_clarification(
            session, mission=mission, strategy=strategy, operation=operation, verdict=verdict
        )
    elif verdict.type is DecisionType.REPLAN:
        await _invalidate_and_replan(
            session,
            mission=mission,
            strategy=strategy,
            operation=operation,
            record=record,
            verdict=verdict,
        )


# ---------------------------------------------------------------------------
# 5. Strategy invalidation + adaptive replanning  (the signature behaviour)
# ---------------------------------------------------------------------------
async def _invalidate_and_replan(
    session: AsyncSession,
    *,
    mission: RecoveryMission,
    strategy: RecoveryStrategy,
    operation: PhoneOperation,
    record: CallRecord | None,
    verdict: decision.Decision,
    trigger: ReplanTrigger = ReplanTrigger.CONSTRAINT_VIOLATED,
) -> None:
    planner.invalidate(strategy, verdict.reason)
    attempt = await session.get(RecoveryAttempt, operation.attempt_id)
    if attempt:
        attempt.status = AttemptStatus.INVALIDATED
        attempt.completed_at = utcnow()

    constraint_id = verdict.constraint_ids[0] if verdict.constraint_ids else None
    evidence_id = verdict.evidence_ids[0] if verdict.evidence_ids else None

    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.STRATEGY_INVALIDATED,
        title=f"Recovery plan invalidated — {strategy.target}",
        actor=Actor.CONSTRAINT_ENGINE,
        strategy_id=strategy.id,
        operation_id=operation.id,
        call_id=record.call_id if record else None,
        constraint_id=constraint_id,
        evidence_id=evidence_id,
        payload={
            "target": strategy.target,
            "reason": verdict.reason,
            "required": verdict.payload.get("required"),
            "observed": verdict.payload.get("observed"),
            "source": record.display_ref if record else None,
        },
    )

    guard = await guardrails.pre_replan_checks(mission)
    if not guard.ok:
        await _escalate(session, mission, guard.reason, rule=guard.rule)
        return

    mission.status = MissionStatus.REPLANNING
    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.REPLAN_STARTED,
        title="Replanning",
        actor=Actor.REPLANNER,
        strategy_id=strategy.id,
    )

    proposal = await planner.replan(
        session,
        mission,
        failed_strategy=strategy,
        exception_type=mission.exception.type,
        violation_reason=verdict.reason,
        observed=verdict.payload.get("observed"),
        required_value=verdict.payload.get("required"),
        constraint_key=verdict.payload.get("constraint_key"),
        trigger=str(trigger),
    )
    template = get_template(mission.exception.type)

    if not proposal.target:
        await _escalate(session, mission, proposal.no_candidate_reason, rule="no_viable_path")
        return

    duplicate = await guardrails.check_duplicate_strategy(
        session, mission, strategy_type=template.strategy_type, target=proposal.target
    )
    if not duplicate.ok:
        await _escalate(session, mission, duplicate.reason, rule=duplicate.rule)
        return

    mission.replan_count += 1
    new_strategy = await planner.create_strategy(
        session,
        mission,
        proposal,
        strategy_type=template.strategy_type,
        version=strategy.version + 1,
    )
    await planner.register_information_needs(
        session, mission, new_strategy, exception_type=mission.exception.type
    )

    replan_row = Replan(
        id=new_id("replan"),
        mission_id=mission.id,
        sequence=mission.replan_count,
        trigger=trigger,
        previous_strategy_id=strategy.id,
        new_strategy_id=new_strategy.id,
        trigger_evidence_id=evidence_id,
        trigger_call_id=record.call_id if record else None,
        violated_constraint_id=constraint_id,
        reason=verdict.reason,
        required_value={"value": verdict.payload.get("required")},
        observed_value={"value": verdict.payload.get("observed")},
        generated_by=proposal.generated_by,
    )
    session.add(replan_row)
    await session.flush()

    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.RECOVERY_REPLANNED,
        title=f"New strategy {new_strategy.version:02d} — {new_strategy.target}",
        actor=Actor.REPLANNER,
        strategy_id=new_strategy.id,
        replan_id=replan_row.id,
        evidence_id=evidence_id,
        call_id=record.call_id if record else None,
        payload={
            "before": {"target": strategy.target, "status": strategy.status},
            "after": {"target": new_strategy.target, "status": new_strategy.status},
            "reason": verdict.reason,
            "required": verdict.payload.get("required"),
            "observed": verdict.payload.get("observed"),
            "triggered_by": record.display_ref if record else None,
            "generated_by": proposal.generated_by,
        },
    )

    # The second call exists *because* of the first call's evidence.
    await _launch_operation(
        session,
        mission=mission,
        strategy=new_strategy,
        facts=list(new_strategy.information_needed or []),
        reason=verdict.reason,
        triggered_by_evidence_id=evidence_id,
        triggered_by_replan_id=replan_row.id,
    )


# ---------------------------------------------------------------------------
# 6. Clarification, call failure, recovery, escalation
# ---------------------------------------------------------------------------
async def _launch_clarification(
    session: AsyncSession,
    *,
    mission: RecoveryMission,
    strategy: RecoveryStrategy,
    operation: PhoneOperation,
    verdict: decision.Decision,
) -> None:
    fact = verdict.payload.get("constraint_key")
    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.CLARIFICATION_REQUIRED,
        title=f"Clarification required — {fact}",
        strategy_id=strategy.id,
        operation_id=operation.id,
        payload={"reason": verdict.reason, "fact": fact},
    )
    await _launch_operation(
        session,
        mission=mission,
        strategy=strategy,
        facts=[fact] if fact else list(strategy.information_needed or []),
        reason=verdict.reason,
        purpose=OperationPurpose.CLARIFICATION,
        clarify_fact=fact,
        attempt=await session.get(RecoveryAttempt, operation.attempt_id),
    )


async def _handle_call_failure(
    session: AsyncSession, *, mission: RecoveryMission, operation: PhoneOperation, error: str
) -> None:
    """A failed call is operational information, but never business evidence."""
    strategy = await session.get(RecoveryStrategy, operation.strategy_id)
    next_attempt = operation.attempt_number + 1
    retry = guardrails.check_retry_budget(mission, next_attempt)

    # Redialling a number the network refuses cannot change the answer, and each
    # attempt is a real charge. Only a line that might be picked up next time is
    # worth a retry.
    if any(code in error.lower() for code in _PERMANENT_FAILURES):
        retry = guardrails.GuardResult(
            False, f"{error} - redialling cannot help.", "unreachable_number"
        )

    if retry.ok:
        await _launch_operation(
            session,
            mission=mission,
            strategy=strategy,
            facts=list(operation.requested_facts or []),
            reason=f"Retry after {error}.",
            purpose=OperationPurpose.RETRY,
            attempt_number=next_attempt,
            attempt=await session.get(RecoveryAttempt, operation.attempt_id),
        )
        return

    await _invalidate_and_replan(
        session,
        mission=mission,
        strategy=strategy,
        operation=operation,
        record=None,
        verdict=decision.Decision(
            DecisionType.REPLAN,
            f"{operation.target} could not be reached after {operation.attempt_number} attempts.",
        ),
        trigger=ReplanTrigger.CALL_FAILURE,
    )


async def _confirm_recovery(
    session: AsyncSession,
    *,
    mission: RecoveryMission,
    strategy: RecoveryStrategy,
    results: list[constraints.EvaluationResult],
    verdict: decision.Decision,
) -> None:
    facts = {
        r.constraint.key: r.observed_value
        for r in results
        if r.status is ConstraintStatus.SATISFIED
    }
    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.RECOVERY_PATH_CONFIRMED,
        title=f"Recovery path confirmed — {strategy.target}",
        actor=Actor.DECISION_ENGINE,
        strategy_id=strategy.id,
        payload={"target": strategy.target, "facts": facts},
    )

    # Cost discovered on the phone can push a viable path behind human approval.
    cost = await _latest_cost(session, mission.id, strategy.target)
    gate = await policy.enforce(
        session,
        mission,
        "confirm_recovery_path",
        context={"target": strategy.target, "cost_increase_pct": cost, **facts},
    )
    if gate.outcome is not PolicyOutcome.ALLOW:
        approval = ApprovalRequest(
            id=new_id("appr"),
            mission_id=mission.id,
            strategy_id=strategy.id,
            action="confirm_recovery_path",
            reason=gate.reason,
            payload={"target": strategy.target, "facts": facts, "cost_increase_pct": cost},
            status="pending",
        )
        session.add(approval)
        mission.status = MissionStatus.APPROVAL_REQUIRED
        await record_event(
            session,
            mission_id=mission.id,
            type=EventType.APPROVAL_REQUIRED,
            title="Human approval required",
            actor=Actor.POLICY_ENGINE,
            strategy_id=strategy.id,
            payload={"reason": gate.reason, "approval_id": approval.id, "facts": facts},
        )
        await session.flush()
        return

    await finalise_recovery(
        session, mission=mission, strategy=strategy, facts=facts, verdict=verdict
    )


async def finalise_recovery(
    session: AsyncSession,
    *,
    mission: RecoveryMission,
    strategy: RecoveryStrategy,
    facts: dict[str, Any],
    verdict: decision.Decision | None = None,
) -> RecoveryOutcome:
    strategy.status = StrategyStatus.SUCCESSFUL
    attempt = (
        (
            await session.execute(
                select(RecoveryAttempt).where(RecoveryAttempt.strategy_id == strategy.id)
            )
        )
        .scalars()
        .first()
    )
    if attempt:
        attempt.status = AttemptStatus.SUCCEEDED
        attempt.completed_at = utcnow()

    now = utcnow()
    mission.status = MissionStatus.RECOVERED
    mission.completed_at = now

    margin = _margin_seconds(mission, facts)
    template = get_template(mission.exception.type)
    evidence_ids = verdict.evidence_ids if verdict else []

    outcome = RecoveryOutcome(
        id=new_id("outcome"),
        mission_id=mission.id,
        status=OutcomeStatus.RECOVERED,
        selected_strategy_id=strategy.id,
        selected_target=strategy.target,
        protected_outcome=mission.exception.threatened_outcome or template.protected_outcome,
        headline=f"{(mission.exception.threatened_outcome or 'Operation').upper()} PROTECTED",
        margin_seconds=margin,
        facts=facts,
        evidence_ids=evidence_ids,
        metrics=await _metrics(session, mission),
        completed_at=now,
    )
    session.add(outcome)
    await session.flush()

    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.RECOVERY_COMPLETED,
        title=outcome.headline,
        actor=Actor.DECISION_ENGINE,
        strategy_id=strategy.id,
        payload={
            "target": strategy.target,
            "facts": facts,
            "margin_seconds": margin,
            "metrics": outcome.metrics,
        },
    )
    return outcome


async def _escalate(
    session: AsyncSession, mission: RecoveryMission, reason: str, *, rule: str = ""
) -> RecoveryOutcome:
    """Escalation is a valid outcome. PHONEOPS never invents a solution."""
    now = utcnow()
    mission.status = MissionStatus.ESCALATED
    mission.completed_at = now

    existing = (
        (
            await session.execute(
                select(RecoveryOutcome).where(RecoveryOutcome.mission_id == mission.id)
            )
        )
        .scalars()
        .first()
    )
    if existing:
        return existing

    outcome = RecoveryOutcome(
        id=new_id("outcome"),
        mission_id=mission.id,
        status=OutcomeStatus.ESCALATED,
        protected_outcome=mission.exception.threatened_outcome,
        headline="RECOVERY ESCALATED",
        #: Audit record, not a list of confirmed findings — the UI must render
        #: these as an explanation, never as ticked facts.
        facts={"reason": reason, "rule": rule},
        metrics=await _metrics(session, mission),
        completed_at=now,
    )
    session.add(outcome)
    await session.flush()

    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.RECOVERY_ESCALATED,
        title="Recovery escalated",
        actor=Actor.DECISION_ENGINE,
        payload={
            "reason": reason,
            "rule": rule,
            "remaining_seconds": remaining_seconds(mission.recovery_cutoff),
            "recommended_human_action": "Review cutoff override or alternate departure.",
        },
    )
    return outcome


# ---------------------------------------------------------------------------
# 7. Human approval
# ---------------------------------------------------------------------------
async def resolve_approval(
    session: AsyncSession,
    *,
    mission_id: str,
    approval_id: str,
    approved: bool,
    decided_by: str,
    comment: str = "",
) -> RecoveryMission:
    mission = await _load(session, mission_id)
    approval = await session.get(ApprovalRequest, approval_id)
    if approval is None or approval.mission_id != mission_id:
        raise NotFoundError(f"Approval {approval_id} not found for mission {mission_id}")
    if approval.status != "pending":
        raise ConflictError(f"Approval {approval_id} already {approval.status}")

    approval.status = "approved" if approved else "rejected"
    approval.decided_by = decided_by
    approval.decided_at = utcnow()
    approval.comment = comment

    strategy = await session.get(RecoveryStrategy, approval.strategy_id)
    if approved:
        await record_event(
            session,
            mission_id=mission.id,
            type=EventType.APPROVAL_GRANTED,
            title="Recovery approved by operator",
            actor=Actor.USER,
            strategy_id=strategy.id if strategy else None,
            payload={"decided_by": decided_by, "comment": comment},
        )
        await finalise_recovery(
            session, mission=mission, strategy=strategy, facts=approval.payload.get("facts", {})
        )
    else:
        await record_event(
            session,
            mission_id=mission.id,
            type=EventType.APPROVAL_REJECTED,
            title="Recovery rejected by operator",
            actor=Actor.USER,
            strategy_id=strategy.id if strategy else None,
            payload={"decided_by": decided_by, "comment": comment},
        )
        await _invalidate_and_replan(
            session,
            mission=mission,
            strategy=strategy,
            operation=await _last_operation(session, mission.id),
            record=None,
            verdict=decision.Decision(
                DecisionType.REPLAN,
                f"Operator rejected {strategy.target}: {comment or 'no reason given'}",
            ),
            trigger=ReplanTrigger.ASSUMPTION_INVALIDATED,
        )
    return mission


# ---------------------------------------------------------------------------
# Webhook / polling entry points
# ---------------------------------------------------------------------------
async def handle_call_update(session: AsyncSession, *, call_id: str, state: CallState) -> bool:
    """Entry point for the CALL-E webhook and the poller. Idempotent."""
    record = (
        (await session.execute(select(CallRecord).where(CallRecord.call_id == call_id)))
        .scalars()
        .first()
    )
    if record is None:
        log.warning("call.update.unknown_call", call_id=call_id)
        return False

    operation = await session.get(PhoneOperation, record.operation_id)
    mission = await _load(session, record.mission_id)

    if operation.status in {OperationStatus.COMPLETED, OperationStatus.FAILED}:
        return False
    if not state.is_terminal:
        call_operations.apply_state(record, state)
        await session.flush()
        return False

    await ingest_call_state(
        session, mission=mission, operation=operation, record=record, state=state
    )
    return True


async def poll_pending_calls() -> int:
    """Background poller for deployments without a public webhook URL."""
    from app.integrations.calle import get_calle_client

    client = get_calle_client()
    handled = 0
    async with session_scope() as session:
        pending = (
            (
                await session.execute(
                    select(CallRecord)
                    .join(PhoneOperation, PhoneOperation.id == CallRecord.operation_id)
                    .where(PhoneOperation.status == OperationStatus.CALLING)
                )
            )
            .scalars()
            .all()
        )
        for record in pending:
            try:
                state = await client.get_call(record.call_id)
            except Exception as exc:  # pragma: no cover - network dependent
                log.warning("call.poll.failed", call_id=record.call_id, error=str(exc))
                continue
            async with mission_lock(record.mission_id):
                if await handle_call_update(session, call_id=record.call_id, state=state):
                    handled += 1
    return handled


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _load(session: AsyncSession, mission_id: str) -> RecoveryMission:
    mission = await session.get(RecoveryMission, mission_id)
    if mission is None:
        raise NotFoundError(f"Recovery mission {mission_id} not found")
    await session.refresh(mission, ["exception"])
    return mission


async def _attempt_sequence(session: AsyncSession, strategy_id: str) -> int:
    rows = (
        (
            await session.execute(
                select(RecoveryAttempt).where(RecoveryAttempt.strategy_id == strategy_id)
            )
        )
        .scalars()
        .all()
    )
    return len(rows) + 1


async def _last_operation(session: AsyncSession, mission_id: str) -> PhoneOperation:
    return (
        (
            await session.execute(
                select(PhoneOperation)
                .where(PhoneOperation.mission_id == mission_id)
                .order_by(PhoneOperation.sequence.desc())
            )
        )
        .scalars()
        .first()
    )


async def _clarification_count(session: AsyncSession, mission_id: str, target: str) -> int:
    rows = (
        (
            await session.execute(
                select(PhoneOperation).where(
                    PhoneOperation.mission_id == mission_id,
                    PhoneOperation.target == target,
                    PhoneOperation.purpose == OperationPurpose.CLARIFICATION,
                )
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


async def _latest_cost(session: AsyncSession, mission_id: str, target: str | None) -> float | None:
    if not target:
        return None
    row = (
        (
            await session.execute(
                select(Evidence)
                .where(
                    Evidence.mission_id == mission_id,
                    Evidence.subject == target,
                    Evidence.type == "cost_increase_pct",
                )
                .order_by(Evidence.observed_at.desc())
            )
        )
        .scalars()
        .first()
    )
    return row.value.get("value") if row else None


def _margin_seconds(mission: RecoveryMission, facts: dict[str, Any]) -> int | None:
    for key in ("pickup_time", "arrival_time", "delivery_time"):
        clock = parse_time(facts.get(key))
        if clock:
            cutoff = as_aware(mission.recovery_cutoff)
            from app.services.timeutil import combine_cutoff

            return int((cutoff - combine_cutoff(cutoff, clock)).total_seconds())
    return None


async def _metrics(session: AsyncSession, mission: RecoveryMission) -> dict[str, Any]:
    strategies = (
        (
            await session.execute(
                select(RecoveryStrategy).where(RecoveryStrategy.mission_id == mission.id)
            )
        )
        .scalars()
        .all()
    )
    approvals = (
        (
            await session.execute(
                select(ApprovalRequest).where(ApprovalRequest.mission_id == mission.id)
            )
        )
        .scalars()
        .all()
    )
    evidence = (
        (await session.execute(select(Evidence).where(Evidence.mission_id == mission.id)))
        .scalars()
        .all()
    )

    # Anchored on detection, not on when someone got round to starting the
    # mission. The question an operations team asks is "how long was the
    # shipment at risk", and the realistic failure in production is a mission
    # nobody launched for forty minutes - which an engine-execution figure
    # cannot show. `engine_seconds` keeps that figure for regression tracking,
    # away from the dashboard, where two durations would only be confused.
    ttr = None
    engine = None
    if mission.completed_at:
        completed = as_aware(mission.completed_at)
        if mission.exception is not None:
            ttr = int((completed - as_aware(mission.exception.detected_at)).total_seconds())
        if mission.started_at:
            engine = int((completed - as_aware(mission.started_at)).total_seconds())

    return {
        "phone_operations": mission.operation_count,
        "strategies_evaluated": len(strategies),
        "automatic_replans": mission.replan_count,
        "human_interventions": len([a for a in approvals if a.status != "pending"]),
        "evidence_count": len(evidence),
        "time_to_recovery_seconds": ttr,
        "engine_seconds": engine,
    }
