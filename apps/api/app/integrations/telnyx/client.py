"""The three Call Control commands the carrier endpoints need."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import settings
from app.logging_config import get_logger

log = get_logger("telnyx.client")

_BASE = "https://api.telnyx.com/v2"


async def command(call_control_id: str, action: str, payload: dict[str, Any] | None = None) -> bool:
    """Send one Call Control command. Returns whether Telnyx accepted it.

    Failures are logged and swallowed: a carrier endpoint that raises would
    leave the webhook returning 5xx, and Telnyx would redeliver the same event
    — which is how one greeting becomes three.
    """
    if not settings.TELNYX_API_KEY:
        log.error("telnyx.command_skipped", action=action, reason="TELNYX_API_KEY is not set")
        return False

    url = f"{_BASE}/calls/{call_control_id}/actions/{action}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.TELNYX_API_KEY}"},
                json=payload or {},
            )
    except httpx.HTTPError as exc:
        log.error("telnyx.command_failed", action=action, error=str(exc))
        return False

    if response.status_code >= 400:
        log.error(
            "telnyx.command_rejected",
            action=action,
            status=response.status_code,
            body=response.text[:400],
        )
        return False

    log.info("telnyx.command", action=action, call_control_id=call_control_id)
    return True


async def answer(call_control_id: str) -> bool:
    return await command(call_control_id, "answer")


async def speak(call_control_id: str, ssml: str, *, voice: str, language: str) -> bool:
    return await command(
        call_control_id,
        "speak",
        {"payload": ssml, "payload_type": "ssml", "voice": voice, "language": language},
    )


async def hangup(call_control_id: str) -> bool:
    return await command(call_control_id, "hangup")
