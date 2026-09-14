from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Header, Request, status

from app.api.deps import SessionDep
from app.config import settings
from app.domain.schemas import CalleWebhookPayload
from app.engine import director
from app.integrations.calle.base import CallState, normalise_outcome
from app.logging_config import get_logger
from app.models import CallRecord

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = get_logger("webhooks.calle")


def _signature_ok(raw_body: bytes, signature: str | None) -> bool:
    """Whether a signed delivery checks out.

    CALL-E stopped sending signature headers; its own SDK marks the HMAC helpers
    deprecated and says current webhooks are unsigned. Demanding one returned
    403 to every real callback. Verification still runs when a signature *is*
    present, so a signing proxy in front of this endpoint keeps working.
    """
    if not settings.CALLE_WEBHOOK_SECRET or not signature:
        return False
    expected = hmac.new(
        settings.CALLE_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature.split("=")[-1].strip())


@router.post("/calle", status_code=status.HTTP_202_ACCEPTED)
async def calle_webhook(
    request: Request,
    session: SessionDep,
    x_calle_signature: str | None = Header(default=None, alias="X-Calle-Signature"),
):
    """Terminal call result notification.

    The body is treated as a **notification, not evidence**. Unsigned deliveries
    tell us only which call changed; the result is then read back from CALL-E
    over the authenticated API, so a forged POST cannot inject a carrier's
    answer into a recovery plan. For a product whose whole claim is that every
    fact is sourced, accepting an unauthenticated payload would be worse than
    the 403 it replaces.

    Idempotent: a duplicate delivery never produces duplicate evidence or a
    duplicate replan, because ingestion is keyed on the operation status.
    """
    raw = await request.body()
    trusted = _signature_ok(raw, x_calle_signature)

    payload = CalleWebhookPayload.model_validate_json(raw or b"{}")
    call_id = payload.resolved_call_id()
    if not call_id:
        return {"accepted": False, "reason": "missing call id"}

    if not trusted:
        from app.integrations.calle import get_calle_client

        try:
            state = await get_calle_client().get_call(call_id)
        except Exception as exc:
            log.warning("webhook.readback_failed", call_id=call_id, error=str(exc))
            return {"accepted": False, "reason": "could not read the call back from CALL-E"}
        log.info("webhook.unsigned", call_id=call_id, action="read back over the API")
        return await _ingest(session, call_id=call_id, state=state)

    structured = payload.resolved_result()
    status_value = (payload.status or payload.event or payload.type or "completed").lower()
    status_value = status_value.replace("call.", "").replace("_completed", "completed")

    state = CallState(
        call_id=call_id,
        status=status_value,
        outcome=normalise_outcome(status_value, structured),
        structured_result=structured,
        result_validation=payload.result_validation or {},
        summary=payload.summary or "",
        transcript_excerpt=(payload.transcript or "")[:600],
        raw=payload.model_dump(mode="json"),
    )

    return await _ingest(session, call_id=call_id, state=state)


async def _ingest(session, *, call_id: str, state: CallState) -> dict:
    """One ingestion path for both a signed payload and a read-back result."""
    record = await session.get(CallRecord, call_id)  # try PK first, then call_id
    mission_id = record.mission_id if record else None
    if mission_id is None:
        from sqlalchemy import select

        found = (
            (await session.execute(select(CallRecord).where(CallRecord.call_id == call_id)))
            .scalars()
            .first()
        )
        mission_id = found.mission_id if found else None

    if mission_id is None:
        log.warning("webhook.unknown_call", call_id=call_id)
        return {"accepted": False, "reason": "unknown call id"}

    async with director.mission_lock(mission_id):
        ingested = await director.handle_call_update(session, call_id=call_id, state=state)
    return {"accepted": True, "ingested": ingested, "call_id": call_id}
