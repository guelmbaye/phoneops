"""Properties the recovery loop must hold whatever the phone says."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.domain.enums import EventType, MissionStatus
from app.domain.errors import ConflictError
from app.engine import director
from app.events.bus import event_bus
from app.integrations.calle.base import CallState
from app.models import MissionEvent, PhoneOperation
from app.seeds.flagship import build_flagship_payload
from app.services import timeutil
from app.services.mission_service import create_exception_and_mission, get_mission
from app.services.view import build_recovery_view, explain
from tests.conftest import flagship_payload

TOO_LATE = {"available": True, "capacity_ok": True, "pickup_time": "18:00", "confirmed": True}


async def _run(session, **kw):
    payload = flagship_payload(**kw)
    mission = await create_exception_and_mission(session, payload)
    await director.start_mission(session, mission.id)
    return await build_recovery_view(session, await get_mission(session, mission.id))


@pytest.mark.asyncio
async def test_the_information_gap_is_recorded_before_any_call(session, calle):
    view = await _run(session, carrier_b_script=TOO_LATE)
    types = [e.type for e in view.timeline]

    gap = types.index(EventType.INFORMATION_GAP_IDENTIFIED)
    first_call = types.index(EventType.CALL_REQUESTED)
    assert gap < first_call, "PHONEOPS must know what it is missing before it dials"

    needs = {n.key for n in view.information_needs}
    assert {"available", "pickup_time", "capacity_ok"} <= needs


@pytest.mark.asyncio
async def test_every_mission_changing_fact_has_call_provenance(session, calle):
    view = await _run(session, carrier_b_script=TOO_LATE)
    call_ids = {c.call_id for op in view.operations for c in op.calls}

    for evidence in view.evidence:
        assert evidence.call_id in call_ids
        assert evidence.operation_id is not None
        assert evidence.subject

    for evaluation in view.constraint_evaluations:
        if evaluation.status in {"satisfied", "violated"}:
            assert evaluation.evidence_ids, "a verdict without evidence is not allowed"


@pytest.mark.asyncio
async def test_a_failed_strategy_is_kept_in_history_never_overwritten(session, calle):
    view = await _run(session, carrier_b_script=TOO_LATE)
    versions = [s.version for s in view.strategies]

    assert versions == [1, 2]
    invalidated = view.strategies[0]
    assert invalidated.target == "Carrier B"
    assert invalidated.invalidated_reason
    assert invalidated.invalidated_at is not None


@pytest.mark.asyncio
async def test_strategy_diff_exposes_before_after_and_cause(session, calle):
    view = await _run(session, carrier_b_script=TOO_LATE)
    diff = view.strategy_diff

    assert diff.before.target == "Carrier B"
    assert diff.after.target == "Carrier C"
    assert diff.required_value["value"] == "17:30"
    assert diff.observed_value["value"] == "18:00"
    assert diff.trigger_call_id and diff.trigger_evidence_id


@pytest.mark.asyncio
async def test_explain_answers_which_call_caused_which_replan(session, calle):
    view = await _run(session, carrier_b_script=TOO_LATE)
    result = await explain(session, view.mission.id)

    assert len(result["replans"]) == 1
    entry = result["replans"][0]
    assert "18:00" in entry["what_happened"]
    assert "17:30" in entry["why_it_matters"]
    assert "Carrier C" in entry["what_happens_next"]
    assert entry["source"]["display_ref"] == "CALL-E #001"
    assert entry["source"]["provider_mode"] == "mock"


@pytest.mark.asyncio
async def test_duplicate_call_results_are_ingested_once(session, calle):
    """A webhook retry must not create duplicate evidence or a duplicate replan."""
    view = await _run(session, carrier_b_script=TOO_LATE)
    first_call = view.operations[0].calls[0]
    evidence_before = len(view.evidence)

    replayed = CallState(
        call_id=first_call.call_id,
        status="completed",
        structured_result={"pickup_time": "18:00", "pickup_time_confirmed": True},
    )
    ingested = await director.handle_call_update(
        session, call_id=first_call.call_id, state=replayed
    )
    assert ingested is False

    after = await build_recovery_view(session, await get_mission(session, view.mission.id))
    assert len(after.evidence) == evidence_before
    assert after.mission.replan_count == 1


@pytest.mark.asyncio
async def test_recovery_margin_is_measured_against_the_cutoff(session, calle):
    view = await _run(session, carrier_b_script=TOO_LATE)
    assert view.outcome.margin_seconds == 45 * 60  # 16:45 vs 17:30
    assert view.mission.status == MissionStatus.RECOVERED


@pytest.mark.asyncio
async def test_demo_metrics_stay_meaningful_not_vain(session, calle):
    view = await _run(session, carrier_b_script=TOO_LATE)
    m = view.metrics

    assert m.phone_operations == 2
    assert m.strategies_evaluated == 2
    assert m.automatic_replans == 1
    assert m.human_interventions == 0


@pytest.mark.asyncio
async def test_concurrent_starts_run_the_mission_exactly_once(session_factory, calle):
    """A double-clicked START RECOVERY must not produce two timelines.

    Reading `started_at` and writing it are separate awaits, so the guard has to
    be an atomic claim rather than a check followed by an assignment.
    """
    async with session_factory() as session:
        mission = await create_exception_and_mission(session, build_flagship_payload())
        mission_id = mission.id
        await session.commit()

    async def start() -> str:
        async with session_factory() as session:
            try:
                await director.start_mission(session, mission_id)
                await session.commit()
                return "ran"
            except ConflictError:
                return "refused"

    results = await asyncio.gather(start(), start())

    assert sorted(results) == ["ran", "refused"]

    async with session_factory() as session:
        view = await build_recovery_view(session, await get_mission(session, mission_id))

    starts = [e for e in view.timeline if e.type == "RECOVERY_STARTED"]
    assert len(starts) == 1
    assert len(view.operations) == 2
    assert [op.target for op in view.operations] == ["Carrier B", "Carrier C"]
    assert view.mission.status == MissionStatus.RECOVERED


@pytest.mark.asyncio
async def test_every_published_event_is_already_readable(session_factory, calle):
    """The stream must never be ahead of the read API.

    Mission Control reacts to an event by re-reading the aggregate. If events are
    published from an uncommitted transaction, that read returns the pre-event
    state and the UI freezes there — it has already spent its only signal.
    """
    async with session_factory() as setup:
        mission = await create_exception_and_mission(setup, build_flagship_payload())
        mission_id = mission.id
        await setup.commit()

    observed: list[tuple[str, bool]] = []
    subscription = event_bus.subscribe(mission_id)

    async def watch() -> None:
        async for event in subscription:
            # A different session, exactly like the HTTP read path.
            async with session_factory() as reader:
                row = await reader.get(MissionEvent, event["id"])
                observed.append((event["type"], row is not None))

    watcher = asyncio.create_task(watch())
    await asyncio.sleep(0)

    async with session_factory() as runner:
        await director.start_mission(runner, mission_id)
        await runner.commit()

    await asyncio.sleep(0.05)  # let the watcher drain what is already queued
    watcher.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await watcher

    assert observed, "no events reached the subscriber"
    unreadable = [name for name, readable in observed if not readable]
    assert unreadable == [], f"published before commit: {unreadable}"
    assert EventType.STRATEGY_INVALIDATED in [name for name, _ in observed]


@pytest.mark.parametrize("zone", ["Africa/Casablanca", "America/New_York", "Asia/Tokyo"])
@pytest.mark.asyncio
async def test_the_flagship_recovery_holds_outside_utc(session, calle, monkeypatch, zone):
    """The whole loop, in an operation that does not run on UTC.

    Every test above runs in UTC, where a wall clock and its stored instant are
    the same number — which is precisely why a timezone defect can pass the
    entire suite and still break the constraint the demo is built on.
    """
    monkeypatch.setattr(settings, "MISSION_TIMEZONE", zone)

    mission = await create_exception_and_mission(session, build_flagship_payload())
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    assert view.constraints[0].required_value["value"] == "17:30"
    assert [op.target for op in view.operations] == ["Carrier B", "Carrier C"]
    assert view.mission.status == MissionStatus.RECOVERED
    assert view.outcome.margin_seconds == 45 * 60


@pytest.mark.asyncio
async def test_each_operation_records_the_contract_it_actually_sent(session, calle):
    """The stored ask must equal the schema CALL-E received.

    Testing the two helpers in isolation is not enough: the defect was that
    `call_operations` recorded the constraint list while sending a schema with
    an extra required fact, so the API reported three requested facts and four
    came back from the same call.
    """
    mission = await create_exception_and_mission(session, build_flagship_payload())
    await director.start_mission(session, mission.id)

    operations = (
        (
            await session.execute(
                select(PhoneOperation).where(PhoneOperation.mission_id == mission.id)
            )
        )
        .scalars()
        .all()
    )

    assert operations
    for operation in operations:
        assert set(operation.requested_facts) == set(operation.result_schema["required"]), (
            f"{operation.target}: recorded {operation.requested_facts}, "
            f"sent {operation.result_schema['required']}"
        )


@pytest.mark.asyncio
async def test_time_to_recovery_measures_exposure_not_engine_speed(session, calle):
    """Anchored on detection, not on when someone got round to starting.

    The question an operations team asks is how long the shipment was at risk,
    and the realistic production failure is a mission nobody launched for forty
    minutes — which an engine-execution figure cannot show. The engine figure
    stays in the API for regression tracking, under its own key.
    """
    mission = await create_exception_and_mission(session, build_flagship_payload())

    # The exception sat for ten minutes before anyone started the recovery.
    detected = timeutil.utcnow() - timedelta(minutes=10)
    mission.exception.detected_at = detected
    await session.flush()

    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    assert view.metrics.time_to_recovery_seconds >= 600
    assert view.metrics.engine_seconds is not None
    assert view.metrics.engine_seconds < view.metrics.time_to_recovery_seconds


@pytest.mark.asyncio
async def test_the_mission_names_what_it_does_not_know_before_calling(session, calle):
    """The sponsor-necessity beat (DOCUMENT 05 §16) must exist before CALL-E.

    Registering the open questions at start meant the brief read "None
    outstanding" until CALL-E was already dialling — the one screen whose whole
    job is to show the recovery blocked on information no system holds.
    """
    mission = await create_exception_and_mission(session, build_flagship_payload())
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    assert view.operations == []
    assert view.current_strategy is not None, "a plan exists before it is executed"
    assert {need.key for need in view.information_needs} == {
        "available",
        "pickup_time",
        "capacity_ok",
    }
    assert all(need.status == "unknown" for need in view.information_needs)
