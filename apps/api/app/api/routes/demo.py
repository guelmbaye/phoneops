from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query, status
from sqlalchemy import delete, select

from app.api.deps import SessionDep
from app.config import settings
from app.db.session import session_scope
from app.domain import schemas
from app.engine import director
from app.models import OperationalException, RecoveryMission
from app.seeds.flagship import FLAGSHIP, build_flagship_payload
from app.services.mission_service import create_exception_and_mission
from app.services.view import build_recovery_view

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/flagship")
async def describe_flagship():
    """What the demo run is, and how the counterfactual is configured."""
    return {
        "scenario": FLAGSHIP,
        "calle_mode": settings.CALLE_MODE,
        "carrier_b_pickup": settings.DEMO_CARRIER_B_PICKUP,
        "expected_behaviour": (
            "replan -> Carrier C"
            if settings.DEMO_CARRIER_B_PICKUP > FLAGSHIP["cutoff_clock"]
            else "keep Carrier B, no second call"
        ),
        "note": (
            "The second call is not scripted: it exists only when a mandatory "
            "constraint is violated by phone-derived evidence."
        ),
    }


@router.post(
    "/flagship", response_model=schemas.MissionSummary, status_code=status.HTTP_201_CREATED
)
async def create_flagship(
    session: SessionDep,
    autostart: bool = Query(True),
    reset: bool = Query(False),
):
    """RESET FLAGSHIP SCENARIO - the demo-reliability button (DOCUMENT 05 §40)."""
    if reset:
        demo_ids = (
            (
                await session.execute(
                    select(RecoveryMission.id).where(RecoveryMission.demo.is_(True))
                )
            )
            .scalars()
            .all()
        )
        if demo_ids:
            exc_ids = (
                (
                    await session.execute(
                        select(RecoveryMission.exception_id).where(RecoveryMission.id.in_(demo_ids))
                    )
                )
                .scalars()
                .all()
            )
            await session.execute(
                delete(OperationalException).where(OperationalException.id.in_(exc_ids))
            )
            await session.flush()

    payload = build_flagship_payload(demo=True, autostart=False)
    mission = await create_exception_and_mission(session, payload)
    from app.services.view import mission_summary

    summary = mission_summary(mission, None)

    if autostart:
        await session.commit()
        mission_id = mission.id

        async def _run() -> None:
            async with director.mission_lock(mission_id):
                async with session_scope() as bg:
                    await director.start_mission(bg, mission_id)

        asyncio.create_task(_run())
    return summary


@router.post("/flagship/run", response_model=schemas.RecoveryView)
async def run_flagship(session: SessionDep):
    """Create and run the full flagship mission synchronously (CI / smoke test)."""
    payload = build_flagship_payload(demo=True)
    mission = await create_exception_and_mission(session, payload)
    async with director.mission_lock(mission.id):
        await director.start_mission(session, mission.id)
    await session.refresh(mission)
    return await build_recovery_view(session, mission)


@router.post("/poll")
async def poll():
    """Manually trigger the pending-call poller (no-webhook deployments)."""
    handled = await director.poll_pending_calls()
    return {"handled": handled}
