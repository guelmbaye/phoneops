"""Graceful failure is a feature, not an accident.

A demo that always succeeds proves nothing; these tests pin the behaviours the
Grand Prize gate asks about (DOCUMENT 09 §50 safety matrix).
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.domain.enums import MissionStatus, OperationPurpose, OutcomeStatus, ReplanTrigger
from app.domain.errors import CalleError
from app.engine import director
from app.integrations.calle.factory import set_calle_client
from app.seeds.flagship import build_flagship_payload
from app.services.mission_service import create_exception_and_mission, get_mission
from app.services.view import build_recovery_view
from tests.conftest import flagship_payload

VIABLE = {"available": True, "capacity_ok": True, "pickup_time": "16:00", "confirmed": True}
TOO_LATE = {"available": True, "capacity_ok": True, "pickup_time": "18:00", "confirmed": True}


async def _run(session, payload):
    mission = await create_exception_and_mission(session, payload)
    await director.start_mission(session, mission.id)
    return await build_recovery_view(session, await get_mission(session, mission.id))


@pytest.mark.asyncio
async def test_no_answer_triggers_retry_then_moves_on(session, calle):
    payload = flagship_payload(carrier_b_script={"__outcome": "no_answer"})
    view = await _run(session, payload)

    b_ops = [o for o in view.operations if o.target == "Carrier B"]
    assert len(b_ops) == 2, "a no-answer must be retried within the retry budget"
    assert b_ops[1].purpose == OperationPurpose.RETRY

    # A no-answer is NOT evidence that the carrier is unavailable.
    assert not [e for e in view.evidence if e.subject == "Carrier B"]
    # ...but it does change mission state: PHONEOPS moves to the next candidate.
    assert any(o.target == "Carrier C" for o in view.operations)
    assert view.mission.status == MissionStatus.RECOVERED


@pytest.mark.asyncio
async def test_ambiguous_answer_produces_a_clarification_call_not_a_conclusion(session, calle):
    payload = flagship_payload(
        carrier_b_script={
            "available": True,
            "capacity_ok": True,
            "pickup_time": "17:30",
            "confirmed": False,  # "probably around 5:30"
        }
    )
    view = await _run(session, payload)

    clarifications = [o for o in view.operations if o.purpose == OperationPurpose.CLARIFICATION]
    assert clarifications, "hedged evidence must trigger clarification, not a decision"
    assert clarifications[0].target == "Carrier B"

    uncertain = [e for e in view.evidence if e.status == "uncertain"]
    assert uncertain, "a hedged pickup time must never be stored as confirmed"


@pytest.mark.asyncio
async def test_when_every_candidate_misses_the_cutoff_the_mission_escalates(session, calle):
    late = {"available": True, "capacity_ok": True, "pickup_time": "19:00", "confirmed": True}
    payload = flagship_payload(carrier_b_script=TOO_LATE)
    for candidate in payload.candidates:
        candidate.meta = {"script": late if candidate.name != "Carrier B" else TOO_LATE}

    view = await _run(session, payload)

    assert view.mission.status == MissionStatus.ESCALATED
    assert view.outcome.status == OutcomeStatus.ESCALATED
    assert view.outcome.selected_target is None, "PHONEOPS must not invent a carrier"
    assert "reason" in view.outcome.facts


@pytest.mark.asyncio
async def test_expired_recovery_window_escalates_without_calling(session, calle):
    payload = flagship_payload(cutoff_clock="00:01")
    view = await _run(session, payload)

    assert view.mission.status == MissionStatus.ESCALATED
    assert view.operations == [], "no phone call once the recovery window has closed"
    assert view.outcome.facts["rule"] == "deadline_expired"


@pytest.mark.asyncio
async def test_replan_budget_stops_an_uncontrolled_loop(session, calle, monkeypatch):
    monkeypatch.setattr(settings, "MAX_REPLANS", 1)
    late = {"available": True, "capacity_ok": True, "pickup_time": "19:00", "confirmed": True}
    payload = flagship_payload(carrier_b_script=TOO_LATE)
    for candidate in payload.candidates:
        candidate.meta = {"script": late if candidate.name != "Carrier B" else TOO_LATE}
    payload.candidates[0].meta = {"script": TOO_LATE}

    mission = await create_exception_and_mission(session, payload)
    mission.limits = {**mission.limits, "max_replans": 1}
    await session.flush()
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    assert view.mission.replan_count <= 1
    assert view.mission.status == MissionStatus.ESCALATED
    assert view.outcome.facts["rule"] in {"max_replans", "no_viable_path"}


@pytest.mark.asyncio
async def test_a_premium_price_moves_the_confirmed_path_behind_human_approval(session, calle):
    payload = flagship_payload(carrier_b_script=TOO_LATE)
    payload.candidates[1].meta = {
        "script": {
            "available": True,
            "capacity_ok": True,
            "pickup_time": "16:45",
            "confirmed": True,
            "cost_increase_pct": 35,
        }
    }
    view = await _run(session, payload)

    assert view.mission.status == MissionStatus.APPROVAL_REQUIRED
    assert view.pending_approval is not None
    assert "35%" in view.pending_approval.reason
    assert view.outcome is None, "nothing is committed before the operator approves"

    # PHONEOPS still found the path autonomously - only the commitment waits.
    assert view.pending_approval.payload["target"] == "Carrier C"
    assert view.pending_approval.payload["facts"]["pickup_time"] == "16:45"


@pytest.mark.asyncio
async def test_operator_approval_completes_the_recovery(session, calle):
    payload = flagship_payload(carrier_b_script=TOO_LATE)
    payload.candidates[1].meta = {
        "script": {
            "available": True,
            "capacity_ok": True,
            "pickup_time": "16:45",
            "confirmed": True,
            "cost_increase_pct": 35,
        }
    }
    view = await _run(session, payload)
    mission_id = view.mission.id

    await director.resolve_approval(
        session,
        mission_id=mission_id,
        approval_id=view.pending_approval.id,
        approved=True,
        decided_by="ops.manager",
        comment="Cost accepted to protect departure.",
    )
    view = await build_recovery_view(session, await get_mission(session, mission_id))

    assert view.mission.status == MissionStatus.RECOVERED
    assert view.outcome.selected_target == "Carrier C"
    assert view.metrics.human_interventions == 1


@pytest.mark.asyncio
async def test_an_unreachable_carrier_is_never_described_as_missing_the_cutoff(session, calle):
    """A no-answer is an operational failure, never business evidence.

    The strategy diff read "Carrier B — Misses the pickup cutoff" for a carrier
    that never picked up, which is exactly the claim the product promises not to
    make. The replan trigger is what distinguishes the two cases.
    """
    payload = build_flagship_payload()
    payload.candidates[0].meta = {"script": {"__outcome": "no_answer"}}

    mission = await create_exception_and_mission(session, payload)
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    assert view.latest_replan is not None
    assert view.latest_replan.trigger == ReplanTrigger.CALL_FAILURE
    # No constraint was evaluated against Carrier B, so nothing may be observed.
    assert view.strategy_diff.observed_value.get("value") is None
    assert "cutoff" not in (view.strategies[0].invalidated_reason or "").lower()


@pytest.mark.asyncio
async def test_an_escalation_records_a_reason_and_no_confirmed_facts(session, calle):
    """`facts` renders as findings; an escalation has none to show."""
    payload = build_flagship_payload()
    for candidate in payload.candidates:
        candidate.meta = {
            "script": {
                "available": True,
                "capacity_ok": True,
                "pickup_time": "19:00",
                "confirmed": True,
            }
        }

    mission = await create_exception_and_mission(session, payload)
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    assert view.outcome.status == OutcomeStatus.ESCALATED
    assert view.outcome.selected_target is None
    assert "reason" in view.outcome.facts
    assert all(candidate.attempted for candidate in view.candidates)


@pytest.mark.asyncio
async def test_unreachable_carriers_are_not_reported_as_failing_constraints(session, calle):
    """Two escalations, two causes, two sentences.

    Saying "no remaining candidate satisfies the mandatory constraints" when
    nobody answered asserts an evaluation that never happened — on a screen
    whose own metrics panel reports zero facts discovered.
    """
    payload = build_flagship_payload()
    for candidate in payload.candidates:
        candidate.meta = {"script": {"__outcome": "no_answer"}}

    mission = await create_exception_and_mission(session, payload)
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    assert view.outcome.status == OutcomeStatus.ESCALATED
    assert view.outcome.facts["reason"] == "No candidate could be reached."
    assert view.evidence == []
    # The replan that led here must not blame constraints either.
    assert "could not be reached" in view.strategies[-1].rationale
    assert "constraints" not in view.strategies[-1].rationale


@pytest.mark.asyncio
async def test_carriers_that_answered_but_missed_the_cutoff_still_say_so(session, calle):
    """The opposite case must not regress into the same sentence."""
    payload = build_flagship_payload()
    late = {"available": True, "capacity_ok": True, "pickup_time": "23:45", "confirmed": True}
    payload.cutoff_clock = "23:30"
    payload.recovery_cutoff = None
    for candidate in payload.candidates:
        candidate.meta = {"script": late}

    mission = await create_exception_and_mission(session, payload)
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    assert view.outcome.facts["reason"] == (
        "No remaining candidate satisfies the mandatory constraints."
    )
    assert view.evidence != []


@pytest.mark.asyncio
async def test_a_declined_carrier_is_not_described_as_failing_or_unreachable(session, calle):
    """A third cause needs a third sentence.

    Carrier C answered, satisfied every constraint, and was declined on cost. It
    neither breached anything nor went unanswered, and collapsing it into either
    story misreports what the operator actually did.
    """
    payload = build_flagship_payload()
    ok = {"available": True, "capacity_ok": True, "pickup_time": "16:00", "confirmed": True}
    payload.candidates[0].name = "Carrier C"
    payload.candidates[0].meta = {"script": {**ok, "cost_increase_pct": 35}}
    payload.candidates[1].name = "Carrier D"
    payload.candidates[1].meta = {"script": ok}

    mission = await create_exception_and_mission(session, payload)
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))
    assert view.pending_approval is not None

    await director.resolve_approval(
        session,
        mission_id=mission.id,
        approval_id=view.pending_approval.id,
        approved=False,
        decided_by="operator",
    )
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    assert view.latest_replan.trigger == ReplanTrigger.ASSUMPTION_INVALIDATED
    rationale = view.current_strategy.rationale
    assert "declined by an operator" in rationale
    assert "could not be reached" not in rationale
    assert "cannot satisfy" not in rationale
    assert view.metrics.human_interventions == 1


@pytest.mark.asyncio
async def test_a_rejected_request_escalates_instead_of_burning_the_candidate_list(session):
    """A 422 is our bug, not a carrier who did not answer.

    Every call returning 422 was reported as "no candidate could be reached" and
    the mission escalated after dialling nobody — a plausible business story
    hiding an integration fault. Retrying an identical payload cannot help, and
    walking the candidate list spends it on our own defect.
    """

    class RejectingClient:
        mode = "http"

        async def create_call(self, request, **kwargs):
            raise CalleError(
                "CALL-E POST /v1/calls -> 422: unknown field 'recipient'",
                transient=False,
                details={"status": 422, "rejected": True, "body": "unknown field"},
            )

        async def get_call(self, call_id):  # pragma: no cover - never reached
            raise AssertionError("no call should have been placed")

        async def aclose(self):
            return None

    set_calle_client(RejectingClient())
    try:
        mission = await create_exception_and_mission(session, build_flagship_payload())
        await director.start_mission(session, mission.id)
        view = await build_recovery_view(session, await get_mission(session, mission.id))
    finally:
        set_calle_client(None)

    assert view.outcome.status == OutcomeStatus.ESCALATED
    assert view.outcome.facts["rule"] == "provider_rejected_request"
    assert "422" in view.outcome.facts["reason"]
    # One attempt, not the whole candidate list.
    assert len(view.operations) == 1
    assert view.operations[0].target == "Carrier B"
    titles = " ".join(event.title for event in view.timeline)
    assert "refused the request" in titles
    assert "could not reach" not in titles
