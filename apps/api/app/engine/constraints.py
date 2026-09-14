"""Deterministic constraint engine.

This module is the causal bridge between a real phone answer and the next AI
action. It is intentionally boring: pure comparisons, no model in the loop
(DOCUMENT 08 §31). An LLM cannot declare a hard constraint satisfied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_id
from app.domain.enums import (
    ConstraintOperator,
    ConstraintStatus,
    EvidenceStatus,
    ValueType,
)
from app.models import ConstraintEvaluation, Evidence, MissionConstraint, RecoveryMission
from app.services.timeutil import (
    as_aware,
    combine_cutoff,
    parse_boolean,
    parse_number,
    parse_time,
    utcnow,
)


@dataclass(slots=True)
class EvaluationResult:
    constraint: MissionConstraint
    status: ConstraintStatus
    observed_value: Any
    evidence: Evidence | None
    explanation: str

    @property
    def is_blocking(self) -> bool:
        return self.constraint.mandatory and self.status is ConstraintStatus.VIOLATED


#: Operators that express "no later than". Only these are bounded by the present:
#: a `gte` requirement ("no earlier than") is not made impossible by the clock.
_DEADLINE_OPERATORS = frozenset({ConstraintOperator.LTE, ConstraintOperator.LT})


def _coerce(value: Any, value_type: str) -> Any:
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if value is None:
        return None
    if value_type == ValueType.TIME:
        return parse_time(value)
    if value_type == ValueType.NUMBER:
        return parse_number(value)
    if value_type == ValueType.BOOLEAN:
        return parse_boolean(value)
    if value_type == ValueType.DATETIME:
        if isinstance(value, datetime):
            return as_aware(value)
        try:
            return as_aware(datetime.fromisoformat(str(value)))
        except ValueError:
            return None
    return str(value)


def compare(operator: str, observed: Any, required: Any) -> bool | None:
    """None means 'not comparable' -> UNCERTAIN, never a silent pass."""
    if observed is None or required is None:
        return None
    try:
        if operator == ConstraintOperator.EQ:
            return observed == required
        if operator == ConstraintOperator.NEQ:
            return observed != required
        if operator == ConstraintOperator.LTE:
            return observed <= required
        if operator == ConstraintOperator.GTE:
            return observed >= required
        if operator == ConstraintOperator.LT:
            return observed < required
        if operator == ConstraintOperator.GT:
            return observed > required
        if operator == ConstraintOperator.IN:
            options = required if isinstance(required, list | tuple | set) else [required]
            return observed in options
    except TypeError:
        return None
    return None


def _format(value: Any) -> Any:
    if hasattr(value, "strftime") and not isinstance(value, datetime):
        return value.strftime("%H:%M")
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def evaluate_constraint(
    constraint: MissionConstraint,
    evidence: Evidence | None,
    *,
    mission: RecoveryMission | None = None,
) -> EvaluationResult:
    required_raw = constraint.required_value.get("value")
    required = _coerce(required_raw, constraint.value_type)

    if evidence is None:
        return EvaluationResult(
            constraint,
            ConstraintStatus.NOT_EVALUATED,
            None,
            None,
            f"No evidence yet for '{constraint.key}'. External confirmation required.",
        )

    # Uncertain / conflicting / stale evidence can never satisfy a hard constraint.
    if evidence.status != EvidenceStatus.VALIDATED:
        return EvaluationResult(
            constraint,
            ConstraintStatus.UNCERTAIN,
            evidence.value.get("value"),
            evidence,
            (
                f"Evidence for '{constraint.key}' is {evidence.status} "
                f"(confidence {evidence.confidence}); clarification required."
            ),
        )

    observed = _coerce(evidence.value.get("value"), constraint.value_type)
    if observed is None:
        return EvaluationResult(
            constraint,
            ConstraintStatus.UNCERTAIN,
            evidence.value.get("value"),
            evidence,
            (
                f"Could not interpret '{evidence.raw_value or evidence.value}' "
                f"as {constraint.value_type}."
            ),
        )

    # Clock times are anchored on the cutoff's operational day before comparison.
    if constraint.value_type == ValueType.TIME and mission is not None:
        cutoff = as_aware(mission.recovery_cutoff)
        observed_cmp = combine_cutoff(cutoff, observed)
        required_cmp = combine_cutoff(cutoff, required) if required is not None else cutoff

        # "before 17:30" is satisfied by any past time, so a carrier promising a
        # 16:45 pickup at 17:01 sailed through and the mission reported a
        # departure protected by a collection that can no longer happen. A
        # deadline is a window with two ends; the near one is now.
        if constraint.operator in _DEADLINE_OPERATORS and observed_cmp < utcnow():
            return EvaluationResult(
                constraint,
                ConstraintStatus.VIOLATED,
                _format(observed),
                evidence,
                (
                    f"Required {constraint.key} <= {_format(required)}; "
                    f"observed {_format(observed)}, already in the past -> VIOLATED."
                ),
            )
    else:
        observed_cmp, required_cmp = observed, required

    verdict = compare(constraint.operator, observed_cmp, required_cmp)
    if verdict is None:
        return EvaluationResult(
            constraint,
            ConstraintStatus.UNCERTAIN,
            _format(observed),
            evidence,
            f"'{constraint.key}' could not be compared with the requirement.",
        )

    status = ConstraintStatus.SATISFIED if verdict else ConstraintStatus.VIOLATED
    symbol = {
        ConstraintOperator.LTE: "<=",
        ConstraintOperator.GTE: ">=",
        ConstraintOperator.LT: "<",
        ConstraintOperator.GT: ">",
        ConstraintOperator.EQ: "=",
        ConstraintOperator.NEQ: "!=",
        ConstraintOperator.IN: "in",
    }.get(constraint.operator, constraint.operator)
    explanation = (
        f"Required {constraint.key} {symbol} {_format(required)}; "
        f"observed {_format(observed)} -> {status.upper()}."
    )
    return EvaluationResult(constraint, status, _format(observed), evidence, explanation)


async def latest_evidence_for(
    session: AsyncSession, *, mission_id: str, subject: str, fact_type: str
) -> Evidence | None:
    """Most recent non-rejected fact of this type, for this subject only.

    Scoping by subject is what prevents Carrier B's answer from ever being used
    to validate a strategy that now targets Carrier C.
    """
    result = await session.execute(
        select(Evidence)
        .where(
            Evidence.mission_id == mission_id,
            Evidence.subject == subject,
            Evidence.type == fact_type,
            Evidence.status != EvidenceStatus.REJECTED,
        )
        .order_by(Evidence.observed_at.desc(), Evidence.created_at.desc())
    )
    return result.scalars().first()


async def evaluate_mission(
    session: AsyncSession,
    mission: RecoveryMission,
    *,
    subject: str,
    strategy_id: str,
    persist: bool = True,
) -> list[EvaluationResult]:
    """Evaluate every mission constraint against the evidence held on `subject`."""
    constraints = (
        (
            await session.execute(
                select(MissionConstraint).where(MissionConstraint.mission_id == mission.id)
            )
        )
        .scalars()
        .all()
    )

    now = utcnow()
    results: list[EvaluationResult] = []

    for constraint in constraints:
        evidence = await latest_evidence_for(
            session, mission_id=mission.id, subject=subject, fact_type=constraint.key
        )
        # Freshness: expired evidence must be reconfirmed, not trusted.
        if (
            evidence
            and evidence.valid_until
            and as_aware(evidence.valid_until) < now
            and evidence.status == EvidenceStatus.VALIDATED
        ):
            evidence.status = EvidenceStatus.STALE
            await session.flush()

        result = evaluate_constraint(constraint, evidence, mission=mission)
        results.append(result)

        if persist:
            session.add(
                ConstraintEvaluation(
                    id=new_id("ceval"),
                    mission_id=mission.id,
                    strategy_id=strategy_id,
                    constraint_id=constraint.id,
                    constraint_key=constraint.key,
                    subject=subject,
                    status=result.status,
                    required_value={"value": constraint.required_value.get("value")},
                    observed_value={"value": result.observed_value},
                    operator=constraint.operator,
                    mandatory=constraint.mandatory,
                    evidence_ids=[evidence.id] if evidence else [],
                    explanation=result.explanation,
                )
            )
    if persist:
        await session.flush()
    return results


def summarise(results: list[EvaluationResult]) -> dict[str, Any]:
    mandatory = [r for r in results if r.constraint.mandatory]
    violations = [r for r in mandatory if r.status is ConstraintStatus.VIOLATED]
    uncertain = [r for r in mandatory if r.status is ConstraintStatus.UNCERTAIN]
    missing = [r for r in mandatory if r.status is ConstraintStatus.NOT_EVALUATED]
    return {
        "all_satisfied": bool(mandatory) and not (violations or uncertain or missing),
        "violations": violations,
        "uncertain": uncertain,
        "missing": missing,
    }
