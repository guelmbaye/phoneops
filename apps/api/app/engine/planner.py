"""Recovery Planner and Replanner.

Both answer a single question - *which candidate do we test next, and what must
we learn from them?* - and both return a proposal. They never dial, never write
mission outcomes, and never decide constraint satisfaction.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.domain.enums import StrategyStatus
from app.integrations.llm.client import LLMUnavailable, llm_enabled, llm_json
from app.integrations.llm.prompts import PLANNER_SYSTEM, REPLANNER_SYSTEM
from app.logging_config import get_logger
from app.models import (
    Evidence,
    InformationNeed,
    MissionConstraint,
    RecoveryCandidate,
    RecoveryMission,
    RecoveryStrategy,
)
from app.services.catalog import get_template
from app.services.timeutil import remaining_seconds, utcnow
from app.services.wording import day_phrase, requirement_phrase

log = get_logger("engine.planner")


@dataclass(slots=True)
class StrategyProposal:
    target: str | None
    candidate_id: str | None
    rationale: str
    information_needed: list[str] = field(default_factory=list)
    generated_by: str = "deterministic"
    no_candidate_reason: str = ""


def strategy_fingerprint(strategy_type: str, target: str | None) -> str:
    raw = f"{strategy_type}:{(target or '').strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def available_candidates(
    session: AsyncSession, mission_id: str, *, exclude: set[str] | None = None
) -> list[RecoveryCandidate]:
    exclude = {name.strip().lower() for name in (exclude or set())}
    rows = (
        (
            await session.execute(
                select(RecoveryCandidate)
                .where(
                    RecoveryCandidate.mission_id == mission_id,
                    RecoveryCandidate.eligible.is_(True),
                    RecoveryCandidate.exhausted.is_(False),
                )
                .order_by(RecoveryCandidate.rank.asc(), RecoveryCandidate.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [c for c in rows if c.name.strip().lower() not in exclude]


async def attempted_targets(session: AsyncSession, mission_id: str) -> set[str]:
    rows = (
        (
            await session.execute(
                select(RecoveryStrategy.target).where(RecoveryStrategy.mission_id == mission_id)
            )
        )
        .scalars()
        .all()
    )
    return {t for t in rows if t}


def _information_needs_for(mission: RecoveryMission, exception_type: str) -> list[str]:
    return [key for key, _, _ in get_template(exception_type).information_needs]


async def _llm_proposal(
    *, system: str, user: str, candidates: list[RecoveryCandidate], required: list[str]
) -> StrategyProposal | None:
    if not llm_enabled():
        return None
    try:
        payload = await llm_json(system=system, user=user, required_keys=["target"])
    except LLMUnavailable as exc:
        log.info("planner.llm_fallback", reason=str(exc))
        return None

    target = payload.get("target")
    if not target:
        return None
    # The model may only pick from the supplied list - validated, never trusted.
    match = next(
        (c for c in candidates if c.name.strip().lower() == str(target).strip().lower()), None
    )
    if match is None:
        log.warning("planner.llm_invalid_target", target=target)
        return None

    needs = payload.get("information_needed") or required
    needs = [n for n in needs if n in required] or required
    return StrategyProposal(
        target=match.name,
        candidate_id=match.id,
        rationale=str(payload.get("rationale") or payload.get("reason") or "")[:240],
        information_needed=needs,
        generated_by="llm",
    )


async def plan_initial(
    session: AsyncSession, mission: RecoveryMission, *, exception_type: str
) -> StrategyProposal:
    """First recovery hypothesis: best-ranked candidate, plus what we must learn."""
    required = _information_needs_for(mission, exception_type)
    candidates = await available_candidates(session, mission.id)
    if not candidates:
        return StrategyProposal(
            None, None, "", required, no_candidate_reason="No eligible recovery candidate."
        )

    proposal = await _llm_proposal(
        system=PLANNER_SYSTEM,
        user=(
            f"Objective: {mission.objective}\n"
            f"Recovery cutoff: {mission.recovery_cutoff.isoformat()}\n"
            f"Seconds remaining: {remaining_seconds(mission.recovery_cutoff)}\n"
            f"Candidates: {[c.name for c in candidates]}\n"
            f"Excluded: []\n"
            f"Fact keys available: {required}"
        ),
        candidates=candidates,
        required=required,
    )
    if proposal:
        return proposal

    best = candidates[0]
    return StrategyProposal(
        target=best.name,
        candidate_id=best.id,
        rationale=f"{best.name} is the highest-ranked eligible candidate not yet contacted.",
        information_needed=required,
    )


async def replan(
    session: AsyncSession,
    mission: RecoveryMission,
    *,
    failed_strategy: RecoveryStrategy,
    exception_type: str,
    violation_reason: str,
    observed: object = None,
    required_value: object = None,
    constraint_key: str | None = None,
    trigger: str | None = None,
) -> StrategyProposal:
    """Given what we just learned, what recovery path is still viable?"""
    required = _information_needs_for(mission, exception_type)
    excluded = await attempted_targets(session, mission.id)
    candidates = await available_candidates(session, mission.id, exclude=excluded)

    if not candidates:
        # Why the list ran out matters. Reporting a constraint failure when no
        # candidate was ever reached asserts an evaluation that never happened —
        # on a screen whose metrics panel says zero facts were discovered.
        reached = (
            await session.execute(
                select(func.count(Evidence.id)).where(Evidence.mission_id == mission.id)
            )
        ).scalar_one()
        return StrategyProposal(
            None,
            None,
            "",
            required,
            no_candidate_reason=(
                "No remaining candidate satisfies the mandatory constraints."
                if reached
                else "No candidate could be reached."
            ),
        )

    proposal = await _llm_proposal(
        system=REPLANNER_SYSTEM,
        user=(
            f"Objective: {mission.objective}\n"
            f"Failed target: {failed_strategy.target}\n"
            f"Failure: {violation_reason}\n"
            f"Observed: {observed} / Required: {required_value}\n"
            f"Seconds remaining: {remaining_seconds(mission.recovery_cutoff)}\n"
            f"Candidates: {[c.name for c in candidates]}\n"
            f"Excluded (already tried): {sorted(excluded)}\n"
            f"Fact keys available: {required}"
        ),
        candidates=candidates,
        required=required,
    )
    if proposal:
        return proposal

    best = candidates[0]
    return StrategyProposal(
        target=best.name,
        candidate_id=best.id,
        # Name the requirement that failed. Everything around this sentence -
        # the timeline, the hero moment, the strategy diff - says which one, so
        # a rationale that only says "the mission constraints" is the one place
        # an operator has to look elsewhere to understand the swap.
        # Three causes, three sentences. A carrier that answered, satisfied every
        # constraint and was declined on cost did not fail to satisfy anything,
        # and was not unreachable.
        rationale=(
            f"{failed_strategy.target} cannot meet {requirement_phrase(constraint_key)}; "
            f"{best.name} is the next eligible candidate."
            if constraint_key
            else f"{failed_strategy.target} could not be reached; "
            f"{best.name} is the next eligible candidate."
            if trigger == "call_failure"
            else f"{failed_strategy.target} was declined by an operator; "
            f"{best.name} is the next eligible candidate."
            if trigger == "assumption_invalidated"
            else (
                f"{failed_strategy.target} cannot satisfy the mission constraints; "
                f"{best.name} is the next eligible candidate."
            )
        ),
        information_needed=required,
    )


async def create_strategy(
    session: AsyncSession,
    mission: RecoveryMission,
    proposal: StrategyProposal,
    *,
    strategy_type: str,
    version: int,
) -> RecoveryStrategy:
    strategy = RecoveryStrategy(
        id=new_id("strategy"),
        mission_id=mission.id,
        version=version,
        type=strategy_type,
        target=proposal.target,
        target_candidate_id=proposal.candidate_id,
        status=StrategyStatus.ACTIVE,
        rationale=proposal.rationale,
        fingerprint=strategy_fingerprint(strategy_type, proposal.target),
        information_needed=proposal.information_needed,
        generated_by=proposal.generated_by,
    )
    session.add(strategy)
    mission.current_strategy_id = strategy.id
    await session.flush()
    return strategy


async def register_information_needs(
    session: AsyncSession,
    mission: RecoveryMission,
    strategy: RecoveryStrategy,
    *,
    exception_type: str,
) -> list[InformationNeed]:
    """Make the unknowns explicit - this is *why* a phone call is justified."""
    template = get_template(exception_type)
    constraint_keys = {
        c.key
        for c in (
            await session.execute(
                select(MissionConstraint).where(MissionConstraint.mission_id == mission.id)
            )
        )
        .scalars()
        .all()
    }
    needs: list[InformationNeed] = []
    for key, description, required_for in template.information_needs:
        if key not in (strategy.information_needed or []):
            continue
        need = InformationNeed(
            id=new_id("need"),
            mission_id=mission.id,
            strategy_id=strategy.id,
            key=key,
            subject=strategy.target or "unknown",
            description=description.format(
                target=strategy.target or "the candidate",
                day=day_phrase(mission.recovery_cutoff),
            ),
            required_for=required_for if required_for in constraint_keys else "",
            status="unknown",
        )
        session.add(need)
        needs.append(need)
    await session.flush()
    return needs


async def resolve_information_needs(
    session: AsyncSession, *, strategy_id: str, resolved_keys: set[str]
) -> None:
    rows = (
        (
            await session.execute(
                select(InformationNeed).where(InformationNeed.strategy_id == strategy_id)
            )
        )
        .scalars()
        .all()
    )
    for need in rows:
        if need.key in resolved_keys:
            need.status = "resolved"
    await session.flush()


def invalidate(strategy: RecoveryStrategy, reason: str) -> None:
    strategy.status = StrategyStatus.INVALIDATED
    strategy.invalidated_reason = reason
    strategy.invalidated_at = utcnow()
