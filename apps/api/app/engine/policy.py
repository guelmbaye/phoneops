"""Policy engine: autonomy is bounded by authority, not by intelligence.

Every executable action passes through `evaluate` before it happens
(DOCUMENT 09 §20). No AI component can bypass this gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import new_id
from app.domain.enums import PolicyOutcome
from app.models import PolicyDecision, RecoveryMission

#: Reversible, information-only actions.
DEFAULT_AUTONOMOUS = [
    "call_candidate",
    "verify_availability",
    "verify_capacity",
    "request_pickup_time",
    "request_clarification",
    "compare_options",
    "replan",
    "recommend",
    # Selecting a viable path is a recommendation, not a commitment. The
    # binding booking below is what needs a human.
    "confirm_recovery_path",
]
#: Consequential actions - PHONEOPS may construct them, a human commits them.
DEFAULT_APPROVAL = [
    "accept_cost_increase",
    "confirm_binding_booking",
    "accept_contract",
    "waive_constraint",
    "material_scope_change",
]
#: Outside mission authority under any circumstance.
DEFAULT_PROHIBITED = [
    "disclose_sensitive_data",
    "authorize_payment",
    "waive_regulatory_requirement",
    "impersonate_third_party",
    "modify_shipment_contents",
]


@dataclass(slots=True)
class PolicyResult:
    outcome: PolicyOutcome
    rule: str
    reason: str
    payload: dict[str, Any]

    @property
    def allowed(self) -> bool:
        return self.outcome is PolicyOutcome.ALLOW


def default_authority() -> dict[str, list[str]]:
    return {
        "autonomous": list(DEFAULT_AUTONOMOUS),
        "approval_required": list(DEFAULT_APPROVAL),
        "prohibited": list(DEFAULT_PROHIBITED),
    }


def evaluate(
    mission: RecoveryMission, action: str, *, context: dict[str, Any] | None = None
) -> PolicyResult:
    context = context or {}
    authority = mission.authority or default_authority()

    if action in authority.get("prohibited", DEFAULT_PROHIBITED):
        return PolicyResult(
            PolicyOutcome.BLOCK,
            "prohibited_action",
            f"'{action}' is outside mission authority.",
            context,
        )

    if action in authority.get("approval_required", DEFAULT_APPROVAL):
        return PolicyResult(
            PolicyOutcome.APPROVAL_REQUIRED,
            "approval_action",
            f"'{action}' requires explicit human authorization.",
            context,
        )

    # Cost is discovered on the phone, so the threshold is checked at commit time.
    cost = context.get("cost_increase_pct")
    if cost is not None and float(cost) > settings.APPROVAL_COST_INCREASE_PCT:
        return PolicyResult(
            PolicyOutcome.APPROVAL_REQUIRED,
            "cost_threshold",
            (
                f"Recovery path carries a +{float(cost):.0f}% cost increase, above the "
                f"{settings.APPROVAL_COST_INCREASE_PCT:.0f}% autonomous limit."
            ),
            context,
        )

    if action in authority.get("autonomous", DEFAULT_AUTONOMOUS):
        return PolicyResult(PolicyOutcome.ALLOW, "autonomous_action", "", context)

    # Unknown action: deny by default, never improvise authority.
    return PolicyResult(
        PolicyOutcome.APPROVAL_REQUIRED,
        "unknown_action",
        f"'{action}' is not covered by mission authority; human review required.",
        context,
    )


async def record(
    session: AsyncSession, mission: RecoveryMission, action: str, result: PolicyResult
) -> PolicyDecision:
    decision = PolicyDecision(
        id=new_id("pol"),
        mission_id=mission.id,
        action=action,
        outcome=result.outcome,
        rule=result.rule,
        reason=result.reason,
        payload=result.payload,
    )
    session.add(decision)
    await session.flush()
    return decision


async def enforce(
    session: AsyncSession,
    mission: RecoveryMission,
    action: str,
    *,
    context: dict[str, Any] | None = None,
) -> PolicyResult:
    result = evaluate(mission, action, context=context)
    await record(session, mission, action, result)
    return result
