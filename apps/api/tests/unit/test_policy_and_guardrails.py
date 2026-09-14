from __future__ import annotations

from app.domain.enums import PolicyOutcome
from app.engine import guardrails, policy
from app.models import RecoveryMission
from app.services import timeutil
from app.services.timeutil import today_at


def _mission(**kw) -> RecoveryMission:
    defaults = dict(
        id="m1",
        exception_id="e1",
        objective="o",
        recovery_cutoff=today_at("23:59"),
        authority=policy.default_authority(),
        limits={"max_replans": 3, "max_call_retries": 2, "max_operations": 8},
        replan_count=0,
        operation_count=0,
    )
    defaults.update(kw)
    return RecoveryMission(**defaults)


def test_information_gathering_is_autonomous():
    assert policy.evaluate(_mission(), "call_candidate").outcome is PolicyOutcome.ALLOW
    assert policy.evaluate(_mission(), "request_clarification").allowed


def test_binding_commitments_require_approval():
    result = policy.evaluate(_mission(), "confirm_binding_booking")
    assert result.outcome is PolicyOutcome.APPROVAL_REQUIRED


def test_prohibited_actions_are_blocked():
    result = policy.evaluate(_mission(), "disclose_sensitive_data")
    assert result.outcome is PolicyOutcome.BLOCK


def test_unknown_actions_deny_by_default():
    result = policy.evaluate(_mission(), "launch_rocket")
    assert result.outcome is PolicyOutcome.APPROVAL_REQUIRED
    assert result.rule == "unknown_action"


def test_cost_above_threshold_requires_approval():
    result = policy.evaluate(_mission(), "confirm_recovery_path", context={"cost_increase_pct": 35})
    assert result.outcome is PolicyOutcome.APPROVAL_REQUIRED
    assert "35%" in result.reason


def test_small_cost_stays_autonomous():
    result = policy.evaluate(_mission(), "confirm_recovery_path", context={"cost_increase_pct": 5})
    assert result.allowed


def test_expired_deadline_blocks_further_recovery():
    mission = _mission(recovery_cutoff=today_at("00:01"))
    assert not guardrails.check_deadline(mission).ok


def test_replan_budget_is_bounded():
    assert not guardrails.check_replan_budget(_mission(replan_count=3)).ok
    assert guardrails.check_replan_budget(_mission(replan_count=2)).ok


def test_operation_budget_is_bounded():
    assert not guardrails.check_operation_budget(_mission(operation_count=8)).ok


def test_retry_budget_is_bounded():
    assert guardrails.check_retry_budget(_mission(), 2).ok
    assert not guardrails.check_retry_budget(_mission(), 3).ok


def test_a_window_that_is_open_but_too_short_does_not_start_a_recovery():
    """Not expired is not the same as long enough to work in.

    With a minute left the guard used to pass, and PHONEOPS placed real calls to
    real people about an outcome it could no longer affect. Escalating is the
    honest answer.
    """
    timeutil.set_clock(timeutil.today_at("17:29"))
    mission = RecoveryMission(
        id="m1", exception_id="e1", objective="o", recovery_cutoff=timeutil.today_at("17:30")
    )

    result = guardrails.check_deadline(mission)

    assert result.ok is False
    assert result.rule == "window_too_short"


def test_a_workable_window_still_starts_a_recovery():
    timeutil.set_clock(timeutil.today_at("09:00"))
    mission = RecoveryMission(
        id="m1", exception_id="e1", objective="o", recovery_cutoff=timeutil.today_at("17:30")
    )

    assert guardrails.check_deadline(mission).ok is True


def test_an_expired_window_is_still_reported_as_expired():
    """The two refusals must stay distinguishable in the audit trail."""
    timeutil.set_clock(timeutil.today_at("18:00"))
    mission = RecoveryMission(
        id="m1", exception_id="e1", objective="o", recovery_cutoff=timeutil.today_at("17:30")
    )

    assert guardrails.check_deadline(mission).rule == "deadline_expired"
