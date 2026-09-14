"""HTTP surface: the contract the frontend and the judges consume."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health(api):
    response = await api.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["calle_mode"] == "mock"


@pytest.mark.asyncio
async def test_full_flagship_run_over_http(api):
    response = await api.post("/api/demo/flagship/run")
    assert response.status_code == 200
    view = response.json()

    assert view["mission"]["status"] == "recovered"
    assert [o["target"] for o in view["operations"]] == ["Carrier B", "Carrier C"]
    assert view["outcome"]["facts"]["pickup_time"] == "16:45"
    assert view["strategy_diff"]["before"]["target"] == "Carrier B"
    assert view["strategy_diff"]["after"]["target"] == "Carrier C"


@pytest.mark.asyncio
async def test_exception_intake_then_start(api):
    payload = {
        "type": "carrier_cancellation",
        "entity_ref": "Shipment #9001",
        "description": "Carrier A cancelled.",
        "threatened_outcome": "Tonight's departure",
        "consequence": "Shipment may miss departure.",
        "cutoff_clock": "17:30",
        "candidates": [
            {"name": "Carrier B", "phone": "+15550100001", "rank": 10},
            {"name": "Carrier C", "phone": "+15550100002", "rank": 20},
        ],
    }
    created = await api.post("/api/exceptions", json=payload)
    assert created.status_code == 201
    mission_id = created.json()["id"]
    assert created.json()["status"] == "assessing"

    started = await api.post(f"/api/recovery-missions/{mission_id}/start")
    assert started.status_code == 200
    view = started.json()
    assert view["mission"]["status"] == "recovered"

    audit = await api.get(f"/api/recovery-missions/{mission_id}/audit")
    assert audit.status_code == 200
    chain = audit.json()["chain"]
    types = [e["type"] for e in chain]
    for expected in (
        "EXCEPTION_DETECTED",
        "CALL_REQUESTED",
        "EVIDENCE_DISCOVERED",
        "CONSTRAINT_VIOLATED",
        "STRATEGY_INVALIDATED",
        "RECOVERY_REPLANNED",
        "RECOVERY_COMPLETED",
    ):
        assert expected in types


@pytest.mark.asyncio
async def test_missing_cutoff_is_rejected(api):
    response = await api.post(
        "/api/exceptions",
        json={
            "type": "carrier_cancellation",
            "entity_ref": "Shipment #1",
            "description": "x",
            "threatened_outcome": "y",
            "consequence": "z",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_unknown_mission_returns_404(api):
    response = await api.get("/api/recovery-missions/mission_does_not_exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_events_endpoint_exposes_the_timeline(api):
    run = await api.post("/api/demo/flagship/run")
    mission_id = run.json()["mission"]["id"]

    events = await api.get(f"/api/recovery-missions/{mission_id}/events?strategic_only=true")
    assert events.status_code == 200
    types = [e["type"] for e in events.json()]
    assert "STRATEGY_INVALIDATED" in types
    assert "RECOVERY_COMPLETED" in types
    assert "CONSTRAINT_EVALUATED" not in types, "noise stays out of the timeline"


@pytest.mark.asyncio
async def test_webhook_rejects_an_unknown_call_id(api):
    response = await api.post(
        "/api/webhooks/calle", json={"call_id": "calle_nope", "status": "completed"}
    )
    assert response.status_code == 202
    assert response.json()["accepted"] is False


@pytest.mark.asyncio
async def test_demo_describe_reports_the_counterfactual_setting(api):
    response = await api.get("/api/demo/flagship")
    assert response.status_code == 200
    assert response.json()["carrier_b_pickup"]
    assert "second call is not scripted" in response.json()["note"]
