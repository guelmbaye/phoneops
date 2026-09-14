"""Recovery Decision Engine.

Determines the terminal or next state of a mission from constraint results and
policy - never from free-form model judgement. A mission becomes RECOVERED only
when every mandatory constraint is SATISFIED by validated evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import ConstraintStatus, DecisionType
from app.engine.constraints import EvaluationResult, summarise
from app.models import RecoveryMission
from app.services.wording import evaluation_title, requirement_phrase


@dataclass(slots=True)
class Decision:
    type: DecisionType
    reason: str
    evidence_ids: list[str] = field(default_factory=list)
    constraint_ids: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


def decide(
    mission: RecoveryMission,
    results: list[EvaluationResult],
    *,
    call_failed: bool = False,
    clarification_budget_left: bool = True,
) -> Decision:
    if call_failed:
        return Decision(
            DecisionType.REPLAN,
            "The phone operation did not produce usable evidence.",
        )

    summary = summarise(results)

    violations = summary["violations"]
    if violations:
        first = violations[0]
        return Decision(
            DecisionType.REPLAN,
            # The engine's own comparison stays on the evaluation record and in
            # the event payload; the reason travels onto the strategy card and
            # the diff, where it is read by a person.
            evaluation_title(
                first.evidence.subject if first.evidence else "This carrier",
                key=first.constraint.key,
                operator=first.constraint.operator,
                required=first.constraint.required_value.get("value"),
                observed=first.observed_value,
                satisfied=False,
            ),
            evidence_ids=[first.evidence.id] if first.evidence else [],
            constraint_ids=[first.constraint.id],
            payload={
                "constraint_key": first.constraint.key,
                "required": first.constraint.required_value.get("value"),
                "observed": first.observed_value,
            },
        )

    uncertain = summary["uncertain"]
    if uncertain:
        first = uncertain[0]
        if clarification_budget_left:
            return Decision(
                DecisionType.CLARIFY,
                first.explanation,
                evidence_ids=[first.evidence.id] if first.evidence else [],
                constraint_ids=[first.constraint.id],
                payload={"constraint_key": first.constraint.key},
            )
        return Decision(
            DecisionType.REPLAN,
            f"{requirement_phrase(first.constraint.key)} remains unconfirmed "
            "after asking again.",
            constraint_ids=[first.constraint.id],
        )

    missing = summary["missing"]
    if missing:
        return Decision(
            DecisionType.CLARIFY if clarification_budget_left else DecisionType.REPLAN,
            f"Missing external confirmation for: {', '.join(r.constraint.key for r in missing)}.",
            constraint_ids=[r.constraint.id for r in missing],
        )

    if summary["all_satisfied"]:
        satisfied = [r for r in results if r.status is ConstraintStatus.SATISFIED]
        return Decision(
            DecisionType.RECOVERED,
            "All mandatory constraints are satisfied by validated phone evidence.",
            evidence_ids=[r.evidence.id for r in satisfied if r.evidence],
            constraint_ids=[r.constraint.id for r in satisfied],
        )

    return Decision(DecisionType.CONTINUE, "Recovery still in progress.")
