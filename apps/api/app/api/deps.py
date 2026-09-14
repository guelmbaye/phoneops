from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.domain.errors import NotFoundError
from app.models import RecoveryMission
from app.services.mission_service import get_mission

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def mission_dep(
    mission_id: Annotated[str, Path(...)], session: SessionDep
) -> RecoveryMission:
    mission = await get_mission(session, mission_id)
    if mission is None:
        raise NotFoundError(f"Recovery mission {mission_id} not found")
    return mission


MissionDep = Annotated[RecoveryMission, Depends(mission_dep)]
