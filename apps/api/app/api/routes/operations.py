from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import MissionDep, SessionDep
from app.domain import schemas
from app.domain.errors import ConflictError, NotFoundError
from app.engine import call_operations, director
from app.integrations.calle.base import CallState, normalise_outcome
from app.models import CallRecord, PhoneOperation, RecoveryMission
from app.services.view import _operations

router = APIRouter(tags=["operations"])


@router.get("/recovery-missions/{mission_id}/operations", response_model=list[schemas.OperationOut])
async def list_operations(mission: MissionDep, session: SessionDep):
    return await _operations(session, mission.id)


@router.post("/operations/{operation_id}/refresh")
async def refresh_operation(operation_id: str, session: SessionDep):
    """Pull the latest CALL-E state for this operation and ingest it if terminal."""
    operation = await session.get(PhoneOperation, operation_id)
    if operation is None:
        raise NotFoundError(f"Operation {operation_id} not found")

    record = (
        (
            await session.execute(
                select(CallRecord)
                .where(CallRecord.operation_id == operation.id)
                .order_by(CallRecord.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if record is None:
        raise NotFoundError(f"No call record for operation {operation_id}")

    state = await call_operations.refresh(session, record)
    ingested = False
    if state.is_terminal:
        async with director.mission_lock(operation.mission_id):
            ingested = await director.handle_call_update(
                session, call_id=record.call_id, state=state
            )
    return {"call_id": record.call_id, "status": state.status, "ingested": ingested}


@router.post("/operations/{operation_id}/result")
async def ingest_result(operation_id: str, payload: schemas.CallResultIngest, session: SessionDep):
    """Manual ingestion path.

    Used by the test-suite and the demo harness to replay a *real* CALL-E result
    deterministically. It writes through exactly the same pipeline as the
    webhook - evidence, constraints, decision - so nothing can be short-circuited.
    """
    operation = await session.get(PhoneOperation, operation_id)
    if operation is None:
        raise NotFoundError(f"Operation {operation_id} not found")
    if operation.status in {"completed", "failed"}:
        raise ConflictError(f"Operation {operation_id} already {operation.status}")

    record = (
        (
            await session.execute(
                select(CallRecord)
                .where(CallRecord.operation_id == operation.id)
                .order_by(CallRecord.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if record is None:
        raise NotFoundError(f"No call record for operation {operation_id}")

    state = CallState(
        call_id=payload.call_id or record.call_id,
        status=str(payload.outcome),
        outcome=normalise_outcome(str(payload.outcome), payload.structured_result),
        structured_result=payload.structured_result,
        summary=payload.summary,
        transcript_excerpt=payload.transcript_excerpt,
    )
    mission = await session.get(RecoveryMission, operation.mission_id)
    await session.refresh(mission, ["exception"])

    async with director.mission_lock(mission.id):
        await director.ingest_call_state(
            session, mission=mission, operation=operation, record=record, state=state
        )
    return {"ok": True, "operation_id": operation.id, "call_id": record.call_id}
