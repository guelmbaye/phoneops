"""Writes MissionEvent rows and pushes them onto the bus.

Every strategy-changing action goes through here, which is what makes the
question "which CALL-E interaction caused this replan?" answerable at any time.

Each event is committed before it is published, so the audit trail is durable
step by step and the read API is never behind the stream.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.domain.enums import STRATEGIC_EVENTS, Actor, EventType
from app.events.bus import event_bus
from app.models import MissionEvent
from app.services.timeutil import utcnow


async def _next_sequence(session: AsyncSession, mission_id: str) -> int:
    result = await session.execute(
        select(func.coalesce(func.max(MissionEvent.sequence), 0)).where(
            MissionEvent.mission_id == mission_id
        )
    )
    return int(result.scalar_one()) + 1


async def record_event(
    session: AsyncSession,
    *,
    mission_id: str,
    type: EventType | str,
    title: str = "",
    actor: Actor | str = Actor.PHONEOPS,
    payload: dict[str, Any] | None = None,
    strategy_id: str | None = None,
    attempt_id: str | None = None,
    operation_id: str | None = None,
    call_id: str | None = None,
    evidence_id: str | None = None,
    constraint_id: str | None = None,
    replan_id: str | None = None,
) -> MissionEvent:
    event = MissionEvent(
        id=new_id("evt"),
        mission_id=mission_id,
        sequence=await _next_sequence(session, mission_id),
        type=str(type),
        actor=str(actor),
        title=title,
        strategic=str(type) in STRATEGIC_EVENTS,
        payload=payload or {},
        strategy_id=strategy_id,
        attempt_id=attempt_id,
        operation_id=operation_id,
        call_id=call_id,
        evidence_id=evidence_id,
        constraint_id=constraint_id,
        replan_id=replan_id,
        created_at=utcnow(),
    )
    session.add(event)
    await session.flush()

    # Commit before publishing. An event is a promise to every subscriber that
    # the state it describes is readable *now*; publishing from an uncommitted
    # transaction breaks that promise, because a UI that reacts by re-reading
    # the API gets the pre-event state and then freezes there, having already
    # spent its only "something changed" signal.
    #
    # It also bounds the blast radius of a crash: a mission that dies mid-run
    # keeps the record of the CALL-E calls it really placed, instead of rolling
    # back the evidence for phone calls that happened in the real world.
    await session.commit()

    await event_bus.publish(
        mission_id,
        {
            "id": event.id,
            "sequence": event.sequence,
            "type": event.type,
            "actor": event.actor,
            "title": event.title,
            "strategic": event.strategic,
            "mission_id": mission_id,
            "strategy_id": strategy_id,
            "operation_id": operation_id,
            "call_id": call_id,
            "evidence_id": evidence_id,
            "constraint_id": constraint_id,
            "replan_id": replan_id,
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        },
    )
    return event
