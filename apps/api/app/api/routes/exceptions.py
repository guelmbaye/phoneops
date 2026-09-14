from __future__ import annotations

import asyncio

from fastapi import APIRouter, status

from app.api.deps import SessionDep
from app.domain.schemas import ExceptionCreate, MissionSummary
from app.engine import director
from app.services.mission_service import create_exception_and_mission
from app.services.view import mission_summary

router = APIRouter(prefix="/exceptions", tags=["exceptions"])


@router.post("", response_model=MissionSummary, status_code=status.HTTP_201_CREATED)
async def create_exception(payload: ExceptionCreate, session: SessionDep) -> MissionSummary:
    """Exception intake. Creates the Recovery Mission, objective and constraints."""
    mission = await create_exception_and_mission(session, payload)
    summary = mission_summary(mission, None)

    if payload.autostart:
        await session.commit()
        mission_id = mission.id

        async def _run() -> None:
            from app.db.session import session_scope

            async with director.mission_lock(mission_id):
                async with session_scope() as bg:
                    await director.start_mission(bg, mission_id)

        asyncio.create_task(_run())
    return summary
