"""The constraint engine must be boringly deterministic."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.domain.enums import (
    Confidence,
    ConstraintOperator,
    ConstraintStatus,
    EvidenceStatus,
    ValueType,
)
from app.engine.constraints import compare, evaluate_constraint
from app.models import Evidence, MissionConstraint, RecoveryMission
from app.services import timeutil
from app.services.timeutil import today_at, utcnow


def _constraint(**kw) -> MissionConstraint:
    defaults = dict(
        id="con_1",
        mission_id="m1",
        key="pickup_time",
        label="Pickup before cutoff",
        operator="lte",
        value_type=ValueType.TIME,
        required_value={"value": "17:30"},
        mandatory=True,
    )
    defaults.update(kw)
    return MissionConstraint(**defaults)


def _evidence(value, *, status=EvidenceStatus.VALIDATED, vtype=ValueType.TIME) -> Evidence:
    return Evidence(
        id="ev_1",
        mission_id="m1",
        subject="Carrier B",
        type="pickup_time",
        value={"value": value},
        value_type=vtype,
        raw_value=str(value),
        confidence=Confidence.HIGH,
        status=status,
        observed_at=utcnow(),
    )


def _mission() -> RecoveryMission:
    return RecoveryMission(
        id="m1", exception_id="e1", objective="o", recovery_cutoff=today_at("17:30")
    )


def test_18h00_violates_a_17h30_cutoff():
    result = evaluate_constraint(_constraint(), _evidence("18:00"), mission=_mission())
    assert result.status is ConstraintStatus.VIOLATED
    assert result.is_blocking
    assert "18:00" in result.explanation and "17:30" in result.explanation


def test_16h45_satisfies_a_17h30_cutoff():
    result = evaluate_constraint(_constraint(), _evidence("16:45"), mission=_mission())
    assert result.status is ConstraintStatus.SATISFIED
    assert not result.is_blocking


def test_boundary_value_is_inclusive():
    result = evaluate_constraint(_constraint(), _evidence("17:30"), mission=_mission())
    assert result.status is ConstraintStatus.SATISFIED


def test_missing_evidence_is_not_evaluated_never_satisfied():
    result = evaluate_constraint(_constraint(), None, mission=_mission())
    assert result.status is ConstraintStatus.NOT_EVALUATED
    assert "External confirmation required" in result.explanation


def test_uncertain_evidence_cannot_satisfy_a_hard_constraint():
    evidence = _evidence("17:30", status=EvidenceStatus.UNCERTAIN)
    result = evaluate_constraint(_constraint(), evidence, mission=_mission())
    assert result.status is ConstraintStatus.UNCERTAIN


def test_conflicting_evidence_cannot_satisfy_a_hard_constraint():
    evidence = _evidence("16:00", status=EvidenceStatus.CONFLICTING)
    result = evaluate_constraint(_constraint(), evidence, mission=_mission())
    assert result.status is ConstraintStatus.UNCERTAIN


def test_uninterpretable_value_is_uncertain_not_violated():
    result = evaluate_constraint(_constraint(), _evidence("sometime later"), mission=_mission())
    assert result.status is ConstraintStatus.UNCERTAIN


def test_boolean_capacity_constraint():
    constraint = _constraint(
        key="capacity_ok",
        operator="eq",
        value_type=ValueType.BOOLEAN,
        required_value={"value": True},
    )
    evidence = _evidence(False, vtype=ValueType.BOOLEAN)
    evidence.type = "capacity_ok"
    result = evaluate_constraint(constraint, evidence, mission=_mission())
    assert result.status is ConstraintStatus.VIOLATED


@pytest.mark.parametrize(
    "op,observed,required,expected",
    [
        ("lte", 1, 2, True),
        ("lte", 3, 2, False),
        ("gte", 5000, 5000, True),
        ("gte", 2000, 5000, False),
        ("eq", "a", "a", True),
        ("neq", "a", "b", True),
        ("in", "x", ["x", "y"], True),
        ("lte", None, 2, None),
    ],
)
def test_compare_matrix(op, observed, required, expected):
    assert compare(op, observed, required) is expected


def test_a_pickup_already_in_the_past_cannot_satisfy_a_cutoff():
    """ "before 17:30" is satisfied by every past time.

    At 17:01 a carrier promising a 16:45 collection sailed through, and the
    mission reported a departure protected by a pickup that could no longer
    happen. A deadline is a window with two ends; the near one is now.
    """
    timeutil.set_clock(timeutil.today_at("17:01"))

    result = evaluate_constraint(_constraint(), _evidence("16:45"), mission=_mission())

    assert result.status is ConstraintStatus.VIOLATED
    assert "already in the past" in result.explanation


def test_a_future_pickup_before_the_cutoff_still_satisfies_it():
    timeutil.set_clock(timeutil.today_at("15:00"))

    result = evaluate_constraint(_constraint(), _evidence("16:45"), mission=_mission())

    assert result.status is ConstraintStatus.SATISFIED


def test_the_present_does_not_bound_a_no_earlier_than_requirement():
    """Only "no later than" operators are bounded by the clock."""
    timeutil.set_clock(timeutil.today_at("17:01"))
    constraint = _constraint()
    constraint.operator = ConstraintOperator.GTE
    constraint.required_value = {"value": "16:00"}

    result = evaluate_constraint(constraint, _evidence("16:45"), mission=_mission())

    assert result.status is ConstraintStatus.SATISFIED


def test_an_evening_answer_fits_a_window_that_crosses_midnight():
    """With a 02:00 cutoff, "23:00" means tonight — three hours before it.

    Anchoring every answer on the cutoff's own date placed it twenty-one hours
    after and turned a viable carrier into a violation. Night operations are
    exactly where a phone recovery matters most.
    """
    timeutil.set_clock(timeutil.today_at("22:00"))
    tomorrow = (timeutil.utcnow() + timedelta(days=1)).date()
    overnight = _mission()
    overnight.recovery_cutoff = timeutil.today_at("02:00", base=tomorrow)
    rule = _constraint()
    rule.required_value = {"value": "02:00"}

    assert (
        evaluate_constraint(rule, _evidence("23:00"), mission=overnight).status
        is ConstraintStatus.SATISFIED
    )
    assert (
        evaluate_constraint(rule, _evidence("03:00"), mission=overnight).status
        is ConstraintStatus.VIOLATED
    )


def test_a_same_day_window_is_unaffected_by_the_midnight_rule():
    timeutil.set_clock(timeutil.today_at("09:00"))

    assert (
        evaluate_constraint(_constraint(), _evidence("18:00"), mission=_mission()).status
        is ConstraintStatus.VIOLATED
    )
