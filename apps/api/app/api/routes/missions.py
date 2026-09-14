from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import MissionDep, SessionDep
from app.db.session import session_scope
from app.domain import schemas
from app.engine import director
from app.models import ApprovalRequest, ConstraintEvaluation, Evidence, RecoveryStrategy, Replan
from app.services.mission_service import list_missions
from app.services.view import build_recovery_view, explain, mission_summary

router = APIRouter(prefix="/recovery-missions", tags=["recovery"])


@router.get("", response_model=list[schemas.MissionSummary])
async def list_all(session: SessionDep, limit: int = Query(50, le=200)):
    missions = await list_missions(session, limit=limit)
    out = []
    for mission in missions:
        current = (
            await session.get(RecoveryStrategy, mission.current_strategy_id)
            if mission.current_strategy_id
            else None
        )
        out.append(mission_summary(mission, current))
    return out


@router.get("/{mission_id}", response_model=schemas.RecoveryView)
async def get_view(mission: MissionDep, session: SessionDep):
    """The single aggregate Mission Control renders."""
    return await build_recovery_view(session, mission)


@router.post("/{mission_id}/start", response_model=schemas.RecoveryView)
async def start(mission: MissionDep, session: SessionDep, background: bool = Query(False)):
    """Launch the recovery loop: plan -> information gap -> CALL-E."""
    if background:
        mission_id = mission.id

        async def _run() -> None:
            async with director.mission_lock(mission_id):
                async with session_scope() as bg:
                    await director.start_mission(bg, mission_id)

        asyncio.create_task(_run())
        return await build_recovery_view(session, mission)

    async with director.mission_lock(mission.id):
        await director.start_mission(session, mission.id)
    await session.refresh(mission)
    return await build_recovery_view(session, mission)


@router.get("/{mission_id}/strategies", response_model=list[schemas.StrategyOut])
async def strategies(mission: MissionDep, session: SessionDep):
    rows = (
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
    return rows


@router.get("/{mission_id}/evidence", response_model=list[schemas.EvidenceOut])
async def evidence(mission: MissionDep, session: SessionDep):
    rows = (
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
    return rows


@router.get(
    "/{mission_id}/constraint-evaluations", response_model=list[schemas.ConstraintEvaluationOut]
)
async def constraint_evaluations(mission: MissionDep, session: SessionDep):
    rows = (
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
    return rows


@router.get("/{mission_id}/replans", response_model=list[schemas.ReplanOut])
async def replans(mission: MissionDep, session: SessionDep):
    rows = (
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
    return rows


@router.get("/{mission_id}/explain")
async def why(mission: MissionDep, session: SessionDep):
    """ "Which CALL-E interaction caused this replan?" - always answerable."""
    return await explain(session, mission.id)


@router.get("/{mission_id}/audit")
async def audit(mission: MissionDep, session: SessionDep):
    """Full causal chain, flat and inspectable: the judge-facing proof."""
    view = await build_recovery_view(session, mission, timeline_limit=500)
    return {
        "mission_id": mission.id,
        "status": mission.status,
        "objective": mission.objective,
        "chain": [
            {
                "sequence": e.sequence,
                "at": e.created_at.isoformat(),
                "type": e.type,
                "actor": e.actor,
                "title": e.title,
                "strategy_id": e.strategy_id,
                "operation_id": e.operation_id,
                "call_id": e.call_id,
                "evidence_id": e.evidence_id,
                "constraint_id": e.constraint_id,
                "replan_id": e.replan_id,
                "payload": e.payload,
            }
            for e in view.timeline
        ],
        "explain": await explain(session, mission.id),
    }


@router.get("/{mission_id}/approvals", response_model=list[schemas.ApprovalOut])
async def approvals(mission: MissionDep, session: SessionDep):
    rows = (
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
    return rows


@router.post(
    "/{mission_id}/approvals/{approval_id}",
    response_model=schemas.RecoveryView,
    status_code=status.HTTP_200_OK,
)
async def decide_approval(
    mission: MissionDep,
    approval_id: str,
    payload: schemas.ApprovalDecisionRequest,
    session: SessionDep,
):
    """Human boundary: PHONEOPS found the path, the operator commits to it."""
    async with director.mission_lock(mission.id):
        await director.resolve_approval(
            session,
            mission_id=mission.id,
            approval_id=approval_id,
            approved=payload.approved,
            decided_by=payload.decided_by,
            comment=payload.comment,
        )
    await session.refresh(mission)
    return await build_recovery_view(session, mission)


@router.post("/{mission_id}/escalate", response_model=schemas.RecoveryView)
async def escalate(mission: MissionDep, session: SessionDep, reason: str = "Manual escalation"):
    async with director.mission_lock(mission.id):
        await director._escalate(session, mission, reason, rule="manual")
    await session.refresh(mission)
    return await build_recovery_view(session, mission)
