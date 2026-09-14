"""Operator-facing wording.

The recovery timeline is read by people, so it must not mix registers: a panel
carrying `available = True` next to an English sentence has two vocabularies in
one place. The machine-precise record lives on `ConstraintEvaluation.explanation`
and in each event's payload, and is asserted here to still be there.
"""

from __future__ import annotations

import pytest

from app.services import timeutil
from app.services.wording import (
    evaluation_title,
    evidence_title,
    fact_label,
    fact_value,
    requirement_bound,
    requirement_phrase,
)


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("available", True, "yes"),
        ("available", False, "no"),
        ("capacity_ok", None, "not stated"),
        ("pickup_time", "18:00", "18:00"),
        ("cost_increase_pct", 0.0, "0%"),
        ("cost_increase_pct", 35, "35%"),
    ],
)
def test_a_value_reads_as_it_was_spoken(key, value, expected):
    assert fact_value(key, value) == expected


def test_a_boolean_never_leaks_its_python_spelling():
    assert "True" not in evidence_title("Carrier B", "available", True)
    assert evidence_title("Carrier B", "available", True) == "Carrier B availability: yes"


def test_an_evidence_line_names_its_subject():
    """Two carriers answer the same question; position alone must not be the
    only way to tell their answers apart when the timeline is scanned."""
    assert evidence_title("Carrier C", "pickup_time", "16:45").startswith("Carrier C")


def test_a_violation_states_the_consequence_then_the_numbers():
    title = evaluation_title(
        "Carrier B",
        key="pickup_time",
        operator="lte",
        required="17:30",
        observed="18:00",
        satisfied=False,
    )

    assert title == "Carrier B cannot meet the pickup cutoff — 18:00, required 17:30 or earlier"


def test_a_satisfied_check_does_not_say_the_same_thing_twice():
    assert (
        evaluation_title(
            "Carrier C",
            key="capacity_ok",
            operator="eq",
            required=True,
            observed=True,
            satisfied=True,
        )
        == "Carrier C meets the capacity requirement"
    )


def test_an_unmapped_constraint_still_reads():
    assert requirement_phrase("dock_slot") == "the dock slot requirement"
    assert fact_label("dock_slot") == "dock slot"
    assert requirement_bound("gte", 5000) == "5000 or more"


@pytest.mark.asyncio
async def test_the_view_words_every_fact_key_it_shows(session, calle):
    """The UI must not have to derive its own names.

    Deriving them locally is how a fact came to be called "availability" in the
    timeline and "Available" on the card beside it, in the same screen.
    """
    from app.engine import director
    from app.seeds.flagship import build_flagship_payload
    from app.services.mission_service import create_exception_and_mission, get_mission
    from app.services.view import build_recovery_view

    mission = await create_exception_and_mission(session, build_flagship_payload())
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    shown = {c.key for c in view.constraints}
    shown.update(e.type for e in view.evidence)
    for operation in view.operations:
        shown.update(operation.requested_facts)

    missing = shown - set(view.fact_labels)
    assert missing == set(), f"no wording served for: {missing}"
    assert view.fact_labels["available"] == "availability"
    assert view.fact_labels["capacity_ok"] == "capacity"


@pytest.mark.asyncio
async def test_the_replan_rationale_names_the_requirement_that_failed(session, calle):
    """ "cannot satisfy the mission constraints" makes the operator look elsewhere.

    The timeline, the invalidation moment and the strategy diff all name the
    requirement; the rationale sitting between them must not be the one place
    that stays vague.
    """
    from app.engine import director
    from app.seeds.flagship import build_flagship_payload
    from app.services.mission_service import create_exception_and_mission, get_mission
    from app.services.view import build_recovery_view

    mission = await create_exception_and_mission(session, build_flagship_payload())
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    rationale = view.current_strategy.rationale
    assert "the pickup cutoff" in rationale
    assert "the mission constraints" not in rationale


@pytest.mark.asyncio
async def test_the_timeline_uses_one_dash(session, calle):
    """A panel that mixes "-" and "—" reads as two hands wrote it."""
    from app.engine import director
    from app.seeds.flagship import build_flagship_payload
    from app.services.mission_service import create_exception_and_mission, get_mission
    from app.services.view import build_recovery_view

    mission = await create_exception_and_mission(session, build_flagship_payload())
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    hyphenated = [event.title for event in view.timeline if " - " in event.title]
    assert hyphenated == []


def test_the_flagship_narrative_follows_the_date_it_rolls_to():
    """ "today's pickup" is false once the scenario rolls to tomorrow.

    The seed moves the cutoff forward so the demo stays playable at any hour;
    leaving the copy behind puts a 23-hour countdown under a sentence claiming
    tonight.
    """
    from app.seeds.flagship import build_flagship_payload

    timeutil.set_clock(timeutil.today_at("09:00"))
    same_day = build_flagship_payload()
    assert "today's pickup" in same_day.description
    assert same_day.threatened_outcome == "Tonight's shipment departure"

    timeutil.set_clock(timeutil.today_at("18:13"))
    rolled = build_flagship_payload()
    assert "tomorrow's pickup" in rolled.description
    assert "Tomorrow evening" in rolled.threatened_outcome
    assert "tonight" not in rolled.consequence.lower()

    timeutil.set_clock(None)


@pytest.mark.parametrize(("offset_days", "expected"), [(0, "today"), (1, "tomorrow")])
def test_the_day_phrase_follows_the_deadline(offset_days, expected, monkeypatch):
    from datetime import timedelta

    from app.services.wording import day_phrase

    timeutil.set_clock(timeutil.today_at("09:00"))
    target = timeutil.today_at(
        "17:30", base=(timeutil.utcnow() + timedelta(days=offset_days)).date()
    )

    assert day_phrase(target) == expected
    timeutil.set_clock(None)


@pytest.mark.asyncio
async def test_calle_is_asked_about_the_day_the_window_closes_on(session, calle):
    """This text is spoken to a real person on a real call.

    "Can you collect this today?" asked about a window that closes tomorrow gets
    an accurate answer to the wrong question — and the whole recovery is then
    built on it.
    """
    from sqlalchemy import select

    from app.engine import director
    from app.models import PhoneOperation
    from app.seeds.flagship import build_flagship_payload
    from app.services.mission_service import create_exception_and_mission

    timeutil.set_clock(timeutil.today_at("18:30"))  # today's window has passed
    mission = await create_exception_and_mission(session, build_flagship_payload())
    await director.start_mission(session, mission.id)

    operation = (
        (
            await session.execute(
                select(PhoneOperation).where(PhoneOperation.mission_id == mission.id)
            )
        )
        .scalars()
        .first()
    )

    asked = " ".join(operation.questions)
    assert "tomorrow" in asked
    assert "today" not in asked
