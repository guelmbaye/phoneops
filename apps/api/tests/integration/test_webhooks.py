"""The CALL-E terminal-result callback.

CALL-E stopped signing its webhooks — its own SDK marks the HMAC helpers
deprecated and states that current deliveries are unsigned — so demanding a
signature returned 403 to every real callback.

Accepting the body instead would let anyone POST a carrier's answer into a
recovery plan. The delivery is a notification: the result is read back from
CALL-E over the authenticated API, so a forged payload changes nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

from app.config import settings
from app.engine import director
from app.models import CallRecord, Evidence
from app.seeds.flagship import build_flagship_payload
from app.services.mission_service import create_exception_and_mission


async def _started_call(session) -> CallRecord:
    mission = await create_exception_and_mission(session, build_flagship_payload())
    await director.start_mission(session, mission.id)
    return (await session.execute(select(CallRecord))).scalars().first()


def _pickup_times(evidence: list[Evidence]) -> list[str]:
    return [item.value.get("value") for item in evidence if item.type == "pickup_time"]


@pytest.mark.asyncio
async def test_an_unsigned_callback_is_accepted(session, api, calle):
    """The 403 it replaces: no signature is sent any more."""
    record = await _started_call(session)

    response = await api.post(
        "/api/webhooks/calle", json={"call_id": record.call_id, "status": "completed"}
    )

    assert response.status_code == 202


@pytest.fixture
def readbacks(monkeypatch, calle):
    """Record whether the handler went back to CALL-E for the truth.

    Asserting on resulting evidence proves nothing here: in mock mode the
    mission has already finished by the time a webhook arrives, so ingestion is
    a no-op and any assertion about evidence passes for the wrong reason. What
    matters is which source the handler trusted.
    """
    seen: list[str] = []
    original = calle.get_call

    async def spy(call_id: str):
        seen.append(call_id)
        return await original(call_id)

    monkeypatch.setattr(calle, "get_call", spy)
    return seen


@pytest.mark.asyncio
async def test_an_unsigned_body_is_never_believed(session, api, calle, readbacks):
    """A forged answer must not reach the constraint engine."""
    record = await _started_call(session)

    forged = {
        "call_id": record.call_id,
        "status": "completed",
        "structured_result": {"pickup_time": "09:00", "pickup_time_confirmed": "confirmed"},
    }
    await api.post("/api/webhooks/calle", json=forged)

    assert readbacks == [record.call_id], "the body was trusted instead of CALL-E"
    evidence = (await session.execute(select(Evidence))).scalars().all()
    assert "09:00" not in _pickup_times(evidence)


@pytest.mark.asyncio
async def test_a_signed_body_is_used_directly(session, api, calle, readbacks, monkeypatch):
    """A signing layer in front of this endpoint keeps working, and skips the
    read-back it no longer needs."""
    monkeypatch.setattr(settings, "CALLE_WEBHOOK_SECRET", "s3cret")
    record = await _started_call(session)

    body = json.dumps(
        {
            "call_id": record.call_id,
            "status": "completed",
            "structured_result": {"pickup_time": "15:15", "pickup_time_confirmed": "confirmed"},
        }
    ).encode()
    signature = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()

    response = await api.post(
        "/api/webhooks/calle",
        content=body,
        headers={"Content-Type": "application/json", "X-Calle-Signature": f"v1={signature}"},
    )

    assert response.status_code == 202
    assert readbacks == [], "a verified body needs no read-back"


@pytest.mark.asyncio
async def test_a_wrong_signature_falls_back_to_reading_it_back(
    session, api, calle, readbacks, monkeypatch
):
    """Not a 403: an invalid signature simply means the body is not evidence."""
    monkeypatch.setattr(settings, "CALLE_WEBHOOK_SECRET", "s3cret")
    record = await _started_call(session)

    response = await api.post(
        "/api/webhooks/calle",
        json={"call_id": record.call_id, "status": "completed"},
        headers={"X-Calle-Signature": "v1=deadbeef"},
    )

    assert response.status_code == 202
    assert readbacks == [record.call_id]


@pytest.mark.asyncio
async def test_a_callback_for_an_unknown_call_changes_nothing(session, api, calle):
    response = await api.post(
        "/api/webhooks/calle", json={"call_id": "call_not_ours", "status": "completed"}
    )

    assert response.json()["accepted"] is False
