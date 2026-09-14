"""Call Operations Agent.

Turns a mission-level information gap into a bounded CALL-E task, then hands it
to the adapter. Nothing here decides anything about the mission: the reason a
call exists is always an unresolved information need.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.base import new_id
from app.domain.enums import CallOutcome, OperationPurpose, OperationStatus
from app.domain.errors import CalleError
from app.integrations.calle import build_result_schema, facts_asked, get_calle_client
from app.integrations.calle.base import CallRequest, CallState, Recipient
from app.logging_config import get_logger
from app.models import (
    CallRecord,
    PhoneOperation,
    RecoveryAttempt,
    RecoveryCandidate,
    RecoveryMission,
    RecoveryStrategy,
)
from app.services.catalog import get_template
from app.services.timeutil import utcnow
from app.services.wording import day_phrase

log = get_logger("engine.call_operations")


def build_task(
    mission: RecoveryMission,
    strategy: RecoveryStrategy,
    *,
    entity_ref: str,
    cutoff_clock: str,
    purpose: OperationPurpose,
    clarify_fact: str | None = None,
) -> str:
    """The goal handed to CALL-E. Goal-driven, not a rigid script."""
    if purpose is OperationPurpose.CLARIFICATION and clarify_fact:
        return (
            f"You are calling {strategy.target} back on behalf of the shipper's operations "
            f"team about {entity_ref}. Their previous answer about '{clarify_fact}' was not "
            f"firm. Politely ask them to confirm a precise, committed value. The pickup must "
            f"happen no later than {cutoff_clock}. Do not negotiate price and do not commit "
            f"to any booking; only obtain a clear confirmation."
        )

    template = get_template(mission.exception.type if mission.exception else "carrier_cancellation")
    # The day the window closes on, not the day the code was written.
    day = day_phrase(mission.recovery_cutoff)
    questions = template.question_set(strategy.target or "", entity_ref, cutoff_clock, day)
    return (
        f"You are calling {strategy.target} on behalf of the shipper's operations team. "
        f"Their previous carrier cancelled and {entity_ref} must leave {day}; the warehouse "
        f"pickup cutoff is {cutoff_clock}. Find out, precisely: "
        + " ".join(questions)
        + " Report the earliest committed pickup time as a clock time. If they can only give "
        "an approximate time, say so explicitly rather than rounding. Also ask whether any "
        "extra cost applies compared with their standard rate, and report it as a percentage. "
        "Do not agree to any price increase and do not confirm a booking - you are gathering "
        "information only."
    )


async def create_operation(
    session: AsyncSession,
    *,
    mission: RecoveryMission,
    strategy: RecoveryStrategy,
    attempt: RecoveryAttempt,
    entity_ref: str,
    cutoff_clock: str,
    facts: list[str],
    purpose: OperationPurpose = OperationPurpose.VERIFY_RECOVERY_FEASIBILITY,
    reason: str = "",
    triggered_by_evidence_id: str | None = None,
    triggered_by_replan_id: str | None = None,
    attempt_number: int = 1,
    clarify_fact: str | None = None,
) -> PhoneOperation:
    candidate = await _candidate_for(session, mission.id, strategy)
    if candidate is None:
        raise CalleError(f"No reachable phone number for target '{strategy.target}'")

    sequence = mission.operation_count + 1
    template = get_template(mission.exception.type if mission.exception else "carrier_cancellation")

    operation = PhoneOperation(
        id=new_id("op"),
        mission_id=mission.id,
        strategy_id=strategy.id,
        attempt_id=attempt.id,
        sequence=sequence,
        target=strategy.target or candidate.name,
        target_phone=candidate.phone,
        purpose=purpose,
        task=build_task(
            mission,
            strategy,
            entity_ref=entity_ref,
            cutoff_clock=cutoff_clock,
            purpose=purpose,
            clarify_fact=clarify_fact,
        ),
        requested_facts=facts_asked(facts),
        questions=template.question_set(
            strategy.target or "", entity_ref, cutoff_clock, day_phrase(mission.recovery_cutoff)
        ),
        result_schema=build_result_schema(facts),
        status=OperationStatus.QUEUED,
        attempt_number=attempt_number,
        idempotency_key=f"{mission.id}:{strategy.id}:{sequence}:{attempt_number}",
        triggered_by_evidence_id=triggered_by_evidence_id,
        triggered_by_replan_id=triggered_by_replan_id,
        reason=reason,
    )
    session.add(operation)
    mission.operation_count = sequence
    candidate.attempted = True
    await session.flush()
    return operation


async def _candidate_for(
    session: AsyncSession, mission_id: str, strategy: RecoveryStrategy
) -> RecoveryCandidate | None:
    if strategy.target_candidate_id:
        found = await session.get(RecoveryCandidate, strategy.target_candidate_id)
        if found:
            return found
    return (
        (
            await session.execute(
                select(RecoveryCandidate).where(
                    RecoveryCandidate.mission_id == mission_id,
                    RecoveryCandidate.name == strategy.target,
                )
            )
        )
        .scalars()
        .first()
    )


def display_ref(sequence: int) -> str:
    return f"CALL-E #{sequence:03d}"


async def dispatch(
    session: AsyncSession, *, mission: RecoveryMission, operation: PhoneOperation
) -> tuple[CallRecord, CallState]:
    """Place the call through the single CALL-E adapter and persist provenance."""
    client = get_calle_client()
    candidate = await _candidate_for(
        session,
        mission.id,
        await session.get(RecoveryStrategy, operation.strategy_id),
    )

    request = CallRequest(
        task=operation.task,
        recipient=Recipient(
            phone=operation.target_phone,
            region=(candidate.region if candidate else settings.CALLE_DEFAULT_REGION),
            locale=(candidate.locale if candidate else settings.CALLE_DEFAULT_LOCALE),
        ),
        result_schema=operation.result_schema,
        idempotency_key=operation.idempotency_key,
        webhook_url=settings.CALLE_WEBHOOK_URL,
        metadata={
            "mission_id": mission.id,
            "operation_id": operation.id,
            "strategy_id": operation.strategy_id,
            "attempt_id": operation.attempt_id,
            "target": operation.target,
            "purpose": operation.purpose,
            "product": "PHONEOPS",
            # Mock personas are data-driven; ignored by the real API.
            **(
                {"script": (candidate.meta or {}).get("script")}
                if candidate and (candidate.meta or {}).get("script")
                else {}
            ),
        },
    )

    operation.status = OperationStatus.CALLING
    operation.requested_at = utcnow()
    await session.flush()

    state = await client.create_call(request)

    record = CallRecord(
        id=new_id("call"),
        mission_id=mission.id,
        operation_id=operation.id,
        provider="CALL-E",
        provider_mode=client.mode,
        call_id=state.call_id,
        display_ref=display_ref(operation.sequence),
        status=state.status,
        outcome=state.outcome,
        structured_result=state.structured_result,
        result_validation=state.result_validation,
        summary=state.summary,
        transcript_excerpt=state.transcript_excerpt,
        duration_seconds=state.duration_seconds,
        error=state.error,
        raw={k: v for k, v in (state.raw or {}).items() if k != "transcript"},
        started_at=state.started_at or utcnow(),
        ended_at=state.ended_at,
    )
    session.add(record)
    await session.flush()

    log.info(
        "call.dispatched",
        mission_id=mission.id,
        operation_id=operation.id,
        call_id=state.call_id,
        mode=client.mode,
        target=operation.target,
        status=state.status,
    )
    return record, state


async def refresh(session: AsyncSession, record: CallRecord) -> CallState:
    """Poll CALL-E for the current state of an in-flight call."""
    client = get_calle_client()
    state = await client.get_call(record.call_id)
    apply_state(record, state)
    await session.flush()
    return state


def apply_state(record: CallRecord, state: CallState) -> None:
    record.status = state.status
    record.outcome = state.outcome or record.outcome
    if state.structured_result:
        record.structured_result = state.structured_result
    if state.result_validation:
        record.result_validation = state.result_validation
    if state.summary:
        record.summary = state.summary
    if state.transcript_excerpt:
        record.transcript_excerpt = state.transcript_excerpt
    if state.duration_seconds is not None:
        record.duration_seconds = state.duration_seconds
    if state.error:
        record.error = state.error
    if state.ended_at:
        record.ended_at = state.ended_at
    elif state.is_terminal:
        record.ended_at = utcnow()


def is_business_failure(state: CallState) -> bool:
    """A no-answer is NOT proof the carrier is unavailable (DOCUMENT 09 §17)."""
    return state.outcome in {CallOutcome.NO_ANSWER, CallOutcome.BUSY, CallOutcome.VOICEMAIL} or (
        state.outcome is CallOutcome.FAILED
    )
