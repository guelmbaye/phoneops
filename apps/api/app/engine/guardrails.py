"""Anti-loop and resource guardrails.

Prevents the uncontrolled call/replan loop and makes escalation a first-class,
predictable outcome instead of an accident.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.engine.planner import strategy_fingerprint
from app.models import RecoveryMission, RecoveryStrategy
from app.services.timeutil import remaining_seconds


@dataclass(slots=True)
class GuardResult:
    ok: bool
    reason: str = ""
    rule: str = ""


def _limit(mission: RecoveryMission, key: str, default: int) -> int:
    return int((mission.limits or {}).get(key, default))


def check_deadline(mission: RecoveryMission) -> GuardResult:
    left = remaining_seconds(mission.recovery_cutoff)
    if left <= 0:
        return GuardResult(
            False,
            "Recovery window has expired; the original objective is no longer recoverable.",
            "deadline_expired",
        )

    # A window that has not expired is not the same as a window long enough to
    # work in. With a minute left this guard used to pass, and PHONEOPS placed
    # real calls to real people about an outcome it could not affect.
    minimum = _limit(mission, "min_recovery_window_seconds", settings.MIN_RECOVERY_WINDOW_SECONDS)
    if left < minimum:
        return GuardResult(
            False,
            (
                f"Only {left}s remain before the cutoff — too short to obtain and act on "
                "a phone answer."
            ),
            "window_too_short",
        )
    return GuardResult(True)


def check_replan_budget(mission: RecoveryMission) -> GuardResult:
    limit = _limit(mission, "max_replans", settings.MAX_REPLANS)
    if mission.replan_count >= limit:
        return GuardResult(False, f"Replan limit reached ({limit}).", "max_replans")
    return GuardResult(True)


def check_operation_budget(mission: RecoveryMission) -> GuardResult:
    limit = _limit(mission, "max_operations", settings.MAX_OPERATIONS_PER_MISSION)
    if mission.operation_count >= limit:
        return GuardResult(False, f"Phone operation limit reached ({limit}).", "max_operations")
    return GuardResult(True)


def check_retry_budget(mission: RecoveryMission, attempt_number: int) -> GuardResult:
    limit = _limit(mission, "max_call_retries", settings.MAX_CALL_RETRIES)
    if attempt_number > limit:
        return GuardResult(False, f"Call retry limit reached ({limit}).", "max_call_retries")
    return GuardResult(True)


async def check_duplicate_strategy(
    session: AsyncSession, mission: RecoveryMission, *, strategy_type: str, target: str | None
) -> GuardResult:
    """A replan must materially differ from the strategy that just failed."""
    fingerprint = strategy_fingerprint(strategy_type, target)
    existing = (
        (
            await session.execute(
                select(RecoveryStrategy).where(
                    RecoveryStrategy.mission_id == mission.id,
                    RecoveryStrategy.fingerprint == fingerprint,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing:
        return GuardResult(
            False,
            f"Proposed strategy repeats an already-attempted path ({target}).",
            "duplicate_strategy",
        )
    return GuardResult(True)


async def pre_replan_checks(mission: RecoveryMission) -> GuardResult:
    for check in (check_deadline, check_replan_budget, check_operation_budget):
        result = check(mission)
        if not result.ok:
            return result
    return GuardResult(True)
