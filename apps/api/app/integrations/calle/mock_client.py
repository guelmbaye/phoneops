"""Deterministic CALL-E stand-in for tests, CI and offline development.

Explicitly labelled `mock` end to end - every CallRecord persists
`provider_mode`, so a mocked run can never be mistaken for a real one in the
audit trail, the API or the UI. The submission demo runs with CALLE_MODE=http.

Personas are data, not branches: a candidate can carry
`meta.script = {"available": true, "pickup_time": "16:45", ...}` and the mock
answers accordingly. The flagship default reads
`settings.DEMO_CARRIER_B_PICKUP`, which is exactly the knob the counterfactual
test flips (16:00 -> no replan, 18:00 -> replan + second call).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.config import settings
from app.db.base import new_id
from app.domain.enums import CallOutcome
from app.integrations.calle.base import CallRequest, CallState
from app.logging_config import get_logger
from app.services.timeutil import utcnow

log = get_logger("calle.mock")

#: Default flagship personas (DOCUMENT 05 §5 golden path).
DEFAULT_SCRIPTS: dict[str, dict[str, Any]] = {
    "carrier b": {
        "available": True,
        "capacity_ok": True,
        "pickup_time": None,  # filled from settings.DEMO_CARRIER_B_PICKUP
        "confirmed": True,
        "notes": "Driver can be routed today, but not earlier than the stated time.",
    },
    "carrier c": {
        "available": True,
        "capacity_ok": True,
        "pickup_time": "16:45",
        "confirmed": True,
        "cost_increase_pct": 0,
        "notes": "Truck already in the area, full shipment accepted.",
    },
    "carrier d": {
        "available": False,
        "capacity_ok": False,
        "pickup_time": None,
        "confirmed": True,
        "notes": "No driver available today.",
    },
}


class MockCalleClient:
    mode = "mock"

    def __init__(self, latency: float | None = None) -> None:
        self.latency = settings.DEMO_CALL_LATENCY_SECONDS if latency is None else latency
        self._calls: dict[str, CallState] = {}
        self._by_idempotency: dict[str, str] = {}

    async def aclose(self) -> None:
        return None

    async def create_call(self, request: CallRequest) -> CallState:
        if request.idempotency_key and request.idempotency_key in self._by_idempotency:
            return self._calls[self._by_idempotency[request.idempotency_key]]

        if self.latency:
            await asyncio.sleep(self.latency)

        target = str(request.metadata.get("target", "unknown"))
        script = self._script_for(target, request.metadata)
        call_id = new_id("calle")

        if script.get("__outcome") == "no_answer":
            state = CallState(
                call_id=call_id,
                status="no_answer",
                outcome=CallOutcome.NO_ANSWER,
                summary=f"{target} did not answer.",
                started_at=utcnow(),
                ended_at=utcnow(),
                raw={"mock": True, "target": target},
            )
        else:
            structured = self._structured(script, request.result_schema)
            state = CallState(
                call_id=call_id,
                status="completed",
                outcome=CallOutcome.COMPLETED,
                structured_result=structured,
                result_validation={"valid": True, "schema": "ok"},
                summary=self._summary(target, structured),
                transcript_excerpt=self._transcript(target, script),
                duration_seconds=round(38 + len(target) * 0.7, 1),
                started_at=utcnow(),
                ended_at=utcnow(),
                raw={"mock": True, "target": target, "script": script},
            )

        self._calls[call_id] = state
        if request.idempotency_key:
            self._by_idempotency[request.idempotency_key] = call_id
        log.info("calle.mock.call", target=target, call_id=call_id, status=state.status)
        return state

    async def get_call(self, call_id: str) -> CallState:
        if call_id not in self._calls:
            raise KeyError(f"unknown mock call {call_id}")
        return self._calls[call_id]

    async def list_events(self, call_id: str) -> list[dict[str, Any]]:
        state = self._calls.get(call_id)
        if not state:
            return []
        return [
            {"type": "call.started", "call_id": call_id},
            {"type": f"call.{state.status}", "call_id": call_id},
        ]

    # ------------------------------------------------------------ internals
    def _script_for(self, target: str, metadata: dict[str, Any]) -> dict[str, Any]:
        override = metadata.get("script")
        if isinstance(override, dict) and override:
            return dict(override)

        key = re.sub(r"\s+", " ", target.strip().lower())
        script = dict(DEFAULT_SCRIPTS.get(key, {}))
        if not script:
            script = {
                "available": True,
                "capacity_ok": True,
                "pickup_time": "17:00",
                "confirmed": True,
                "notes": "Generic mock persona.",
            }
        if key == "carrier b" and script.get("pickup_time") is None:
            script["pickup_time"] = settings.DEMO_CARRIER_B_PICKUP
        return script

    @staticmethod
    def _structured(script: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        """Answer only what the mission actually asked for."""
        properties = schema.get("properties", {})
        result: dict[str, Any] = {}
        for field in properties:
            if field.endswith("_confirmed"):
                base = field[: -len("_confirmed")]
                if base in properties:
                    result[field] = bool(script.get("confirmed", True)) and (
                        script.get(base) is not None
                    )
                continue
            if field in script:
                result[field] = script[field]
            elif field == "notes":
                result["notes"] = script.get("notes", "")
            else:
                result[field] = None
        return result

    @staticmethod
    def _summary(target: str, structured: dict[str, Any]) -> str:
        bits = [f"{k}={v}" for k, v in structured.items() if v is not None and k != "notes"]
        return f"{target}: " + (", ".join(bits) if bits else "no usable answer")

    @staticmethod
    def _transcript(target: str, script: dict[str, Any]) -> str:
        if script.get("available") is False:
            return f"{target} dispatcher: we have no driver available today."
        pickup = script.get("pickup_time")
        hedge = "" if script.get("confirmed", True) else "probably "
        return (
            f"{target} dispatcher: yes we can take it, earliest we can be there is "
            f"{hedge}{pickup}."
        )
