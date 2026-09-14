from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import MissionDep, SessionDep
from app.domain import schemas
from app.events.bus import event_bus
from app.models import MissionEvent

router = APIRouter(prefix="/recovery-missions", tags=["events"])


@router.get("/{mission_id}/events", response_model=list[schemas.EventOut])
async def list_events(
    mission: MissionDep,
    session: SessionDep,
    since: int = Query(0, ge=0),
    strategic_only: bool = Query(False),
):
    query = (
        select(MissionEvent)
        .where(MissionEvent.mission_id == mission.id, MissionEvent.sequence > since)
        .order_by(MissionEvent.sequence.asc())
    )
    if strategic_only:
        query = query.where(MissionEvent.strategic.is_(True))
    return (await session.execute(query)).scalars().all()


@router.get("/{mission_id}/stream")
async def stream_events(
    mission: MissionDep, session: SessionDep, request: Request, since: int = Query(0, ge=0)
):
    """Server-Sent Events. Replays history from `since`, then streams live.

    The frontend is a projection of backend truth: it renders these events, it
    does not recompute mission state.
    """
    backlog = (
        (
            await session.execute(
                select(MissionEvent)
                .where(MissionEvent.mission_id == mission.id, MissionEvent.sequence > since)
                .order_by(MissionEvent.sequence.asc())
            )
        )
        .scalars()
        .all()
    )
    mission_id = mission.id
    last_seen = backlog[-1].sequence if backlog else since

    async def generator() -> AsyncIterator[str]:
        for event in backlog:
            yield _sse(
                {
                    "id": event.id,
                    "sequence": event.sequence,
                    "type": event.type,
                    "actor": event.actor,
                    "title": event.title,
                    "strategic": event.strategic,
                    "mission_id": mission_id,
                    "strategy_id": event.strategy_id,
                    "operation_id": event.operation_id,
                    "call_id": event.call_id,
                    "evidence_id": event.evidence_id,
                    "constraint_id": event.constraint_id,
                    "replan_id": event.replan_id,
                    "payload": event.payload,
                    "created_at": event.created_at.isoformat(),
                }
            )

        seen = last_seen
        subscription = event_bus.subscribe(mission_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(subscription.__anext__(), timeout=15.0)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                except StopAsyncIteration:  # pragma: no cover
                    break
                if event.get("sequence", 0) <= seen:
                    continue
                seen = event.get("sequence", seen)
                yield _sse(event)
        finally:
            await subscription.aclose()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: dict) -> str:
    return (
        f"id: {event.get('sequence', 0)}\n"
        f"event: {event.get('type', 'message')}\n"
        f"data: {json.dumps(event, default=str)}\n\n"
    )
