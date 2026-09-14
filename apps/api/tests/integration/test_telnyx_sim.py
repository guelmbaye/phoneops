"""The controlled PSTN endpoints that play the demo carriers.

This harness answers real phone calls, so its boundaries are asserted as
carefully as its behaviour: it must stay mounted only when enabled, refuse
numbers it was not asked to play, and never speak twice for one event.
"""

from __future__ import annotations

import base64
import time
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.integrations.telnyx import client, persona_for


def _event(kind: str, *, to: str, event_id: str = "evt_1") -> dict[str, Any]:
    return {
        "data": {
            "id": event_id,
            "event_type": kind,
            "payload": {"call_control_id": "ccid_1", "to": to, "from": "+15550000000"},
        }
    }


@pytest.fixture
def carriers(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_CARRIER_B_PHONE", "+18087880472")
    monkeypatch.setattr(settings, "DEMO_CARRIER_C_PHONE", "+18087880473")
    monkeypatch.setattr(settings, "DEMO_CARRIER_B_PICKUP", "18:00")
    monkeypatch.setattr(settings, "TELNYX_PUBLIC_KEY", "")
    return None


@pytest.fixture
def sent(monkeypatch):
    """Record commands instead of calling Telnyx."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake(call_control_id: str, action: str, payload: dict[str, Any] | None = None):
        calls.append((action, payload or {}))
        return True

    monkeypatch.setattr(client, "command", fake)
    return calls


@pytest.fixture
async def harness(monkeypatch):
    monkeypatch.setattr(settings, "TELNYX_SIM_ENABLED", True)
    from fastapi import FastAPI

    from app.api.routes import telnyx_sim

    app = FastAPI()
    app.include_router(telnyx_sim.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
        yield http


@pytest.mark.asyncio
async def test_the_three_events_answer_speak_and_hang_up(harness, carriers, sent):
    for index, kind in enumerate(["call.initiated", "call.answered", "call.speak.ended"]):
        response = await harness.post(
            "/api/webhooks/telnyx", json=_event(kind, to="+18087880472", event_id=f"e{index}")
        )
        assert response.status_code == 200

    assert [action for action, _ in sent] == ["answer", "speak", "hangup"]


@pytest.mark.asyncio
async def test_the_spoken_line_carries_the_pickup_time_and_a_pause(harness, carriers, sent):
    """CALL-E greets the instant the line is answered; without the break both
    sides talk at once and the pickup time is what gets lost."""
    await harness.post("/api/webhooks/telnyx", json=_event("call.answered", to="+18087880472"))

    _, payload = sent[0]
    assert payload["payload_type"] == "ssml"
    assert '<break time="3s"/>' in payload["payload"]
    assert "6 P M" in payload["payload"], "a synthesiser reading 18:00 says 'eighteen hundred'"


def test_each_schema_field_gets_its_own_sentence(carriers):
    """Bundling availability and capacity into one clause cost the availability
    field on the first live call — CALL-E took the capacity and returned
    `unknown` for the other, which risks a clarification call nobody needs."""
    line = persona_for("+18087880472").spoken_line().lower()

    assert "carrier b is available today" in line, "availability, named and on its own"
    assert "capacity for the full shipment" in line, "capacity, stated on its own"
    assert "earliest pickup time" in line
    assert "no extra charge" in line, "cost, stated on its own"


@pytest.mark.asyncio
async def test_each_number_plays_its_own_carrier(carriers):
    assert persona_for("+1 808 788 0472").name == "Carrier B"
    assert persona_for("+18087880473").name == "Carrier C"
    assert "4 45 P M" in persona_for("+18087880473").spoken_line()


@pytest.mark.asyncio
async def test_a_number_we_were_not_asked_to_play_is_never_answered(harness, carriers, sent):
    response = await harness.post(
        "/api/webhooks/telnyx", json=_event("call.initiated", to="+15559999999")
    )

    assert response.status_code == 200
    assert sent == []


@pytest.mark.asyncio
async def test_a_redelivered_event_does_not_speak_twice(harness, carriers, sent):
    """Telnyx redelivers. Speaking again talks over the first line."""
    event = _event("call.answered", to="+18087880472", event_id="evt_dup")
    await harness.post("/api/webhooks/telnyx", json=event)
    await harness.post("/api/webhooks/telnyx", json=event)

    assert [action for action, _ in sent] == ["speak"]


@pytest.mark.asyncio
async def test_a_bad_signature_is_refused(harness, carriers, sent, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    monkeypatch.setattr(settings, "TELNYX_PUBLIC_KEY", public)

    response = await harness.post(
        "/api/webhooks/telnyx",
        json=_event("call.initiated", to="+18087880472", event_id="evt_sig"),
        headers={
            "telnyx-signature-ed25519": base64.b64encode(b"nonsense" * 8).decode(),
            "telnyx-timestamp": str(int(time.time())),
        },
    )

    assert response.status_code == 403
    assert sent == [], "the endpoint answers a real phone line; it must not act on forged events"


@pytest.mark.asyncio
async def test_the_hangup_still_fires_when_the_event_drops_the_number(harness, carriers, sent):
    """Telnyx sends `call.speak.ended` with `to: null`.

    Re-deriving the carrier from each event dropped the call there: the hangup
    was never sent and the line stayed open until CALL-E gave up — 52 seconds of
    silence on a real call, in the middle of the demo.
    """
    await harness.post(
        "/api/webhooks/telnyx", json=_event("call.answered", to="+18087880472", event_id="a1")
    )

    ended = _event("call.speak.ended", to="+18087880472", event_id="a2")
    ended["data"]["payload"]["to"] = None
    await harness.post("/api/webhooks/telnyx", json=ended)

    assert [action for action, _ in sent] == ["speak", "hangup"]


@pytest.mark.asyncio
async def test_an_unknown_call_without_a_number_is_still_ignored(harness, carriers, sent):
    """The recall must not turn into "answer anything"."""
    ended = _event("call.speak.ended", to=None, event_id="b1")
    ended["data"]["payload"]["call_control_id"] = "ccid_never_seen"

    await harness.post("/api/webhooks/telnyx", json=ended)

    assert sent == []
