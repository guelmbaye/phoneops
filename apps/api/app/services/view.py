"""Builds the single aggregate the Adaptive Recovery Control screen renders.

Constraint truth belongs to the backend (DOCUMENT 08 §40) - the UI never decides
that a plan failed, it only displays the decision and its provenance.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain import schemas
from app.domain.enums import MissionStatus, OperationStatus
from app.models import (
    ApprovalRequest,
    CallRecord,
    ConstraintEvaluation,
    Evidence,
    InformationNeed,
    MissionConstraint,
    MissionEvent,
    PhoneOperation,
    RecoveryCandidate,
    RecoveryMission,
    RecoveryOutcome,
    RecoveryStrategy,
    Replan,
)
from app.services.timeutil import as_aware, remaining_seconds
from app.services.wording import fact_label, requirement_phrase


def _fact_labels(constraints, evidence, operations, needs, outcome) -> dict[str, str]:
    """Every fact key on screen, worded once."""
    keys: set[str] = {c.key for c in constraints}
    keys.update(e.type for e in evidence)
    keys.update(n.key for n in needs)
    for operation in operations:
        keys.update(operation.requested_facts or [])
    if outcome is not None:
        keys.update((outcome.facts or {}).keys())
    return {key: fact_label(key) for key in sorted(keys)}


def _constraint_out(constraint: MissionConstraint) -> schemas.ConstraintOut:
    out = schemas.ConstraintOut.model_validate(constraint)
    out.short_label = requirement_phrase(constraint.key)
    return out


def _deadline(mission: RecoveryMission) -> schemas.DeadlineOut:
    left = remaining_seconds(mission.recovery_cutoff)
    return schemas.DeadlineOut(
        cutoff_at=as_aware(mission.recovery_cutoff),
        remaining_seconds=max(left, 0),
        expired=left <= 0,
        timezone=settings.MISSION_TIMEZONE,
    )


def mission_summary(
    mission: RecoveryMission, current: RecoveryStrategy | None
) -> schemas.MissionSummary:
    return schemas.MissionSummary(
        id=mission.id,
        status=mission.status,
        objective=mission.objective,
        severity=mission.severity,
        entity_ref=mission.exception.entity_ref,
        exception_type=mission.exception.type,
        deadline=_deadline(mission),
        current_strategy_target=current.target if current else None,
        replan_count=mission.replan_count,
        operation_count=mission.operation_count,
        created_at=mission.created_at,
    )


async def _operations(session: AsyncSession, mission_id: str) -> list[schemas.OperationOut]:
    ops = (
        (
            await session.execute(
                select(PhoneOperation)
                .where(PhoneOperation.mission_id == mission_id)
                .order_by(PhoneOperation.sequence.asc())
            )
        )
        .scalars()
        .all()
    )

    out: list[schemas.OperationOut] = []
    for op in ops:
        calls = (
            (
                await session.execute(
                    select(CallRecord)
                    .where(CallRecord.operation_id == op.id)
                    .order_by(CallRecord.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        dto = schemas.OperationOut.model_validate(op)
        dto.calls = [schemas.CallOut.model_validate(c) for c in calls]
        out.append(dto)
    return out


async def build_recovery_view(
    session: AsyncSession, mission: RecoveryMission, *, timeline_limit: int = 100
) -> schemas.RecoveryView:
    strategies = (
        (
            await session.execute(
                select(RecoveryStrategy)
                .where(RecoveryStrategy.mission_id == mission.id)
                .order_by(RecoveryStrategy.version.asc())
            )
        )
        .scalars()
        .all()
    )
    current = next(
        (s for s in strategies if s.id == mission.current_strategy_id),
        strategies[-1] if strategies else None,
    )

    constraints = (
        (
            await session.execute(
                select(MissionConstraint).where(MissionConstraint.mission_id == mission.id)
            )
        )
        .scalars()
        .all()
    )
    needs = (
        (
            await session.execute(
                select(InformationNeed).where(InformationNeed.mission_id == mission.id)
            )
        )
        .scalars()
        .all()
    )
    evidence = (
        (
            await session.execute(
                select(Evidence)
                .where(Evidence.mission_id == mission.id)
                .order_by(Evidence.observed_at.asc())
            )
        )
        .scalars()
        .all()
    )
    evaluations = (
        (
            await session.execute(
                select(ConstraintEvaluation)
                .where(ConstraintEvaluation.mission_id == mission.id)
                .order_by(ConstraintEvaluation.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    replans = (
        (
            await session.execute(
                select(Replan)
                .where(Replan.mission_id == mission.id)
                .order_by(Replan.sequence.asc())
            )
        )
        .scalars()
        .all()
    )
    approvals = (
        (
            await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.mission_id == mission.id)
                .order_by(ApprovalRequest.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    pending_approvals = [a for a in approvals if a.status == "pending"]
    outcome = (
        (
            await session.execute(
                select(RecoveryOutcome).where(RecoveryOutcome.mission_id == mission.id)
            )
        )
        .scalars()
        .first()
    )
    events = (
        (
            await session.execute(
                select(MissionEvent)
                .where(MissionEvent.mission_id == mission.id)
                .order_by(MissionEvent.sequence.asc())
                .limit(timeline_limit)
            )
        )
        .scalars()
        .all()
    )
    candidates = (
        (
            await session.execute(
                select(RecoveryCandidate)
                .where(RecoveryCandidate.mission_id == mission.id)
                .order_by(RecoveryCandidate.rank.asc())
            )
        )
        .scalars()
        .all()
    )

    operations = await _operations(session, mission.id)
    active = next(
        (o for o in operations if o.status in {OperationStatus.QUEUED, OperationStatus.CALLING}),
        None,
    )

    latest_replan = replans[-1] if replans else None
    diff = None
    if latest_replan:
        before = next((s for s in strategies if s.id == latest_replan.previous_strategy_id), None)
        after = next((s for s in strategies if s.id == latest_replan.new_strategy_id), None)
        diff = schemas.StrategyDiffOut(
            before=schemas.StrategyOut.model_validate(before) if before else None,
            after=schemas.StrategyOut.model_validate(after) if after else None,
            reason=latest_replan.reason,
            required_value=latest_replan.required_value,
            observed_value=latest_replan.observed_value,
            trigger_call_id=latest_replan.trigger_call_id,
            trigger_evidence_id=latest_replan.trigger_evidence_id,
        )

    metrics = schemas.MissionMetrics(
        phone_operations=mission.operation_count,
        strategies_evaluated=len(strategies),
        automatic_replans=mission.replan_count,
        human_interventions=len([a for a in approvals if a.status != "pending"]),
        evidence_count=len(evidence),
        # Detection -> confirmed recovery: how long the operation was exposed,
        # not how fast the engine ran. A mission nobody launched for forty
        # minutes is the realistic failure, and an engine figure cannot show it.
        time_to_recovery_seconds=(
            int(
                (
                    as_aware(mission.completed_at) - as_aware(mission.exception.detected_at)
                ).total_seconds()
            )
            if mission.completed_at and mission.exception
            else None
        ),
        engine_seconds=(
            int((as_aware(mission.completed_at) - as_aware(mission.started_at)).total_seconds())
            if mission.started_at and mission.completed_at
            else None
        ),
    )

    return schemas.RecoveryView(
        mission=mission_summary(mission, current),
        exception=schemas.ExceptionOut.model_validate(mission.exception),
        impact={
            "threatened_outcome": mission.exception.threatened_outcome,
            "consequence": mission.exception.consequence,
            "severity": mission.severity,
            "at_risk": mission.status != MissionStatus.RECOVERED,
        },
        constraints=[_constraint_out(c) for c in constraints],
        fact_labels=_fact_labels(constraints, evidence, operations, needs, outcome),
        information_needs=[schemas.InformationNeedOut.model_validate(n) for n in needs],
        current_strategy=schemas.StrategyOut.model_validate(current) if current else None,
        strategies=[schemas.StrategyOut.model_validate(s) for s in strategies],
        active_operation=active,
        operations=operations,
        evidence=[schemas.EvidenceOut.model_validate(e) for e in evidence],
        constraint_evaluations=[
            schemas.ConstraintEvaluationOut.model_validate(e) for e in evaluations
        ],
        latest_replan=schemas.ReplanOut.model_validate(latest_replan) if latest_replan else None,
        strategy_diff=diff,
        pending_approval=(
            schemas.ApprovalOut.model_validate(pending_approvals[0]) if pending_approvals else None
        ),
        outcome=schemas.OutcomeOut.model_validate(outcome) if outcome else None,
        timeline=[schemas.EventOut.model_validate(e) for e in events],
        metrics=metrics,
        candidates=[schemas.CandidateOut.model_validate(c) for c in candidates],
    )


async def explain(session: AsyncSession, mission_id: str) -> dict:
    """ "Why did PHONEOPS change the plan?" - the trust drawer, as data."""
    replans = (
        (
            await session.execute(
                select(Replan)
                .where(Replan.mission_id == mission_id)
                .order_by(Replan.sequence.asc())
            )
        )
        .scalars()
        .all()
    )

    chain = []
    for replan in replans:
        evidence = (
            await session.get(Evidence, replan.trigger_evidence_id)
            if replan.trigger_evidence_id
            else None
        )
        constraint = (
            await session.get(MissionConstraint, replan.violated_constraint_id)
            if replan.violated_constraint_id
            else None
        )
        previous = await session.get(RecoveryStrategy, replan.previous_strategy_id)
        new = (
            await session.get(RecoveryStrategy, replan.new_strategy_id)
            if replan.new_strategy_id
            else None
        )
        call = None
        if replan.trigger_call_id:
            call = (
                (
                    await session.execute(
                        select(CallRecord).where(CallRecord.call_id == replan.trigger_call_id)
                    )
                )
                .scalars()
                .first()
            )

        chain.append(
            {
                "sequence": replan.sequence,
                "what_happened": (
                    f"{previous.target if previous else 'The current target'} answered "
                    f"{replan.observed_value.get('value')}"
                ),
                "why_it_matters": (
                    f"{constraint.label} (required {constraint.operator} "
                    f"{constraint.required_value.get('value')})"
                    if constraint
                    else replan.reason
                ),
                "what_changed": (
                    f"Strategy {previous.version if previous else '?'} "
                    f"({previous.target if previous else '?'}) invalidated"
                ),
                "what_happens_next": (
                    f"Verify {new.target}" if new else "Escalate to a human operator"
                ),
                "source": {
                    "call_id": replan.trigger_call_id,
                    "display_ref": call.display_ref if call else None,
                    "provider_mode": call.provider_mode if call else None,
                    "evidence_id": replan.trigger_evidence_id,
                    "observed": replan.observed_value.get("value"),
                    "required": replan.required_value.get("value"),
                    "confidence": evidence.confidence if evidence else None,
                },
            }
        )
    return {"mission_id": mission_id, "replans": chain}
