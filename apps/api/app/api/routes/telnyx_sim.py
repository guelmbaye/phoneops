"""Controlled PSTN endpoints that answer as the demo carriers.

A **test harness**, mounted only when `TELNYX_SIM_ENABLED` is set. It has no
access to missions, evidence or the recovery engine, and deliberately so: the
carrier's answer must travel the long way — spoken over the telephone network,
heard by CALL-E, extracted into a structured result — or CALL-E is not
load-bearing and the demo is a script with extra steps.

Event flow, one call:

    call.initiated   -> answer
    call.answered    -> speak the carrier's line
    call.speak.ended -> hangup
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.integrations.telnyx import client, persona_for
from app.integrations.telnyx.signature import verify
from app.logging_config import get_logger

router = APIRouter(prefix="/api/webhooks", tags=["telnyx"])
log = get_logger("telnyx.webhook")

#: Telnyx redelivers. Answering twice makes the carrier greet twice, which CALL-E
#: hears as one garbled turn. Bounded so a long demo cannot grow it without end.
_SEEN: OrderedDict[str, None] = OrderedDict()
_SEEN_LIMIT = 512

#: call_control_id -> carrier. `call.speak.ended` arrives with `to: null`, so
#: re-deriving the persona from the event drops the call: the hangup is never
#: sent and the line stays open until CALL-E gives up.
_PLAYING: OrderedDict[str, str] = OrderedDict()


def _already_handled(event_id: str | None) -> bool:
    if not event_id:
        return False
    if event_id in _SEEN:
        return True
    _SEEN[event_id] = None
    while len(_SEEN) > _SEEN_LIMIT:
        _SEEN.popitem(last=False)
    return False


@router.post("/telnyx")
async def telnyx_events(request: Request) -> Response:
    """Answer, speak, hang up. Nothing else, on purpose."""
    body = await request.body()

    if settings.TELNYX_PUBLIC_KEY:
        ok, reason = verify(
            public_key=settings.TELNYX_PUBLIC_KEY,
            signature=request.headers.get("telnyx-signature-ed25519"),
            timestamp=request.headers.get("telnyx-timestamp"),
            body=body,
        )
        if not ok:
            log.warning("telnyx.signature_rejected", reason=reason)
            return Response(status_code=403)
    else:
        log.warning("telnyx.signature_unverified", reason="TELNYX_PUBLIC_KEY is not set")

    payload: dict[str, Any] = await request.json()
    data = payload.get("data") or {}
    event = str(data.get("event_type") or "")
    event_id = data.get("id")
    call = data.get("payload") or {}

    call_control_id = call.get("call_control_id")
    called_number = call.get("to")
    persona = persona_for(called_number)

    # Remember it on the first event that carries a number, and recall it on the
    # ones that do not.
    if persona and call_control_id:
        _PLAYING[call_control_id] = called_number or ""
        while len(_PLAYING) > _SEEN_LIMIT:
            _PLAYING.popitem(last=False)
    elif call_control_id in _PLAYING:
        persona = persona_for(_PLAYING[call_control_id])

    # `event` is structlog's own keyword for the message; using it as a field
    # raises TypeError at the first webhook.
    log.info(
        "telnyx.event",
        event_type=event,
        call_control_id=call_control_id,
        to=called_number,
        carrier=persona.name if persona else None,
    )

    # Always 200: a non-2xx makes Telnyx redeliver, and a redelivered
    # `call.answered` speaks the line a second time over the first.
    if _already_handled(event_id):
        log.info("telnyx.duplicate_ignored", event_type=event, event_id=event_id)
        return Response(status_code=200)

    if not call_control_id:
        return Response(status_code=200)

    if persona is None:
        # Not one of ours. Never answer a number we were not asked to play.
        log.warning("telnyx.unknown_destination", to=called_number, event_type=event)
        return Response(status_code=200)

    if event == "call.initiated":
        await client.answer(call_control_id)
    elif event == "call.answered":
        await client.speak(
            call_control_id,
            persona.spoken_line(),
            voice=settings.TELNYX_VOICE,
            language=settings.TELNYX_LANGUAGE,
        )
    elif event in ("call.speak.ended", "call.speak_ended"):
        await client.hangup(call_control_id)
    elif event == "call.hangup":
        _PLAYING.pop(call_control_id, None)

    return Response(status_code=200)
