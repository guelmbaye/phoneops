"""Exception intake -> Recovery Mission.

Turns "something broke" into a measurable, time-bounded, constraint-aware
objective plus the candidate set the planner is allowed to draw from.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import new_id
from app.domain.enums import Actor, EventType, MissionStatus, ValueType
from app.domain.errors import ValidationError
from app.domain.schemas import ExceptionCreate
from app.engine.policy import default_authority
from app.events.recorder import record_event
from app.models import (
    MissionConstraint,
    OperationalException,
    RecoveryCandidate,
    RecoveryMission,
)
from app.services.catalog import get_template
from app.services.timeutil import today_at, utcnow, wall_clock


def build_objective(payload: ExceptionCreate, cutoff_clock: str) -> str:
    template = get_template(payload.type)
    return template.objective_template.format(entity=payload.entity_ref, cutoff=cutoff_clock)


async def create_exception_and_mission(
    session: AsyncSession, payload: ExceptionCreate
) -> RecoveryMission:
    if payload.recovery_cutoff is None and not payload.cutoff_clock:
        raise ValidationError("Either 'recovery_cutoff' or 'cutoff_clock' is required.")

    cutoff = payload.recovery_cutoff or today_at(payload.cutoff_clock or "17:30")
    cutoff_clock = wall_clock(cutoff)

    exception = OperationalException(
        id=new_id("exc"),
        type=payload.type,
        entity_ref=payload.entity_ref,
        description=payload.description,
        severity=payload.severity,
        detected_at=payload.detected_at or utcnow(),
        threatened_outcome=payload.threatened_outcome,
        consequence=payload.consequence,
        source=payload.source,
        payload=payload.payload,
    )
    session.add(exception)

    mission = RecoveryMission(
        id=new_id("mission"),
        exception_id=exception.id,
        objective=build_objective(payload, cutoff_clock),
        status=MissionStatus.ASSESSING,
        severity=payload.severity,
        recovery_cutoff=cutoff,
        autonomy_level=settings.AUTONOMY_LEVEL,
        authority=default_authority(),
        limits={
            "max_replans": settings.MAX_REPLANS,
            "max_call_retries": settings.MAX_CALL_RETRIES,
            "max_operations": settings.MAX_OPERATIONS_PER_MISSION,
        },
        scope={
            "objective": payload.threatened_outcome,
            "allowed_actions": ["verify", "compare", "clarify", "replan", "recommend"],
            "forbidden_actions": ["modify_shipment_contents", "change_customer_contract"],
        },
        demo=payload.demo,
    )
    session.add(mission)
    await session.flush()
    mission.exception = exception  # avoid a lazy load on the freshly created graph

    await _create_constraints(session, mission, payload, cutoff_clock)
    await _create_candidates(session, mission, payload)

    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.EXCEPTION_DETECTED,
        title=f"{payload.type.replace('_', ' ').title()} — {payload.entity_ref}",
        actor=Actor.USER if payload.source == "manual" else Actor.PHONEOPS,
        payload={
            "entity_ref": payload.entity_ref,
            "description": payload.description,
            "severity": payload.severity,
            "impact": payload.threatened_outcome,
            "consequence": payload.consequence,
            "cutoff": cutoff_clock,
        },
    )
    await record_event(
        session,
        mission_id=mission.id,
        type=EventType.RECOVERY_MISSION_CREATED,
        title=mission.objective,
        payload={"objective": mission.objective, "cutoff": cutoff.isoformat()},
    )
    # Plan before anyone can start it: the brief and its open questions are the
    # product's own argument, and they must exist before the first call rather
    # than appear once CALL-E is already dialling.
    from app.engine.director import prepare_mission

    await prepare_mission(session, mission)
    return mission


async def _create_constraints(
    session: AsyncSession,
    mission: RecoveryMission,
    payload: ExceptionCreate,
    cutoff_clock: str,
) -> None:
    provided = {c.key for c in payload.constraints}
    template = get_template(payload.type)

    for spec in template.constraints:
        if spec.key in provided:
            continue
        required = spec.default_required_value
        if required is None and spec.value_type == ValueType.TIME:
            required = cutoff_clock  # deadline-derived constraint
        if required is None:
            continue
        session.add(
            MissionConstraint(
                id=new_id("con"),
                mission_id=mission.id,
                key=spec.key,
                label=spec.label,
                operator=spec.operator,
                value_type=spec.value_type,
                required_value={"value": required},
                mandatory=spec.mandatory,
            )
        )

    for constraint in payload.constraints:
        session.add(
            MissionConstraint(
                id=new_id("con"),
                mission_id=mission.id,
                key=constraint.key,
                label=constraint.label or constraint.key.replace("_", " ").title(),
                operator=constraint.operator,
                value_type=constraint.value_type,
                required_value={"value": constraint.required_value},
                mandatory=constraint.mandatory,
            )
        )
    await session.flush()


async def _create_candidates(
    session: AsyncSession, mission: RecoveryMission, payload: ExceptionCreate
) -> None:
    for index, candidate in enumerate(payload.candidates):
        session.add(
            RecoveryCandidate(
                id=new_id("cand"),
                mission_id=mission.id,
                name=candidate.name,
                phone=candidate.phone,
                region=candidate.region,
                locale=candidate.locale,
                rank=candidate.rank if candidate.rank != 100 else (index + 1) * 10,
                eligible=candidate.eligible,
                meta=candidate.meta,
            )
        )
    await session.flush()


async def get_mission(session: AsyncSession, mission_id: str) -> RecoveryMission | None:
    mission = await session.get(RecoveryMission, mission_id)
    if mission:
        await session.refresh(mission, ["exception"])
    return mission


async def list_missions(session: AsyncSession, *, limit: int = 50) -> list[RecoveryMission]:
    rows = (
        (
            await session.execute(
                select(RecoveryMission).order_by(RecoveryMission.created_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    for mission in rows:
        await session.refresh(mission, ["exception"])
    return rows
