"""CALL-E adapter contract.

Every phone interaction in PHONEOPS goes through this single boundary
(DOCUMENT 08 §17-18): credentials, retries, idempotency, result normalisation
and call-id provenance live in one place, and nothing else in the codebase is
allowed to dial.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.domain.enums import CallOutcome

#: CALL-E terminal statuses normalised to our vocabulary.
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "no_answer", "busy", "voicemail"}


@dataclass(slots=True)
class Recipient:
    phone: str
    region: str = "US"
    locale: str = "en-US"

    def as_payload(self) -> dict[str, Any]:
        # `phones` is a list and the field is `recipients` upstream. Sending
        # `recipient: {phone: ...}` is rejected outright: CreateCallRequest is
        # `additionalProperties: false`, so an unknown key fails the whole
        # request with 422 before any number is dialled.
        return {"phones": [self.phone], "region": self.region, "locale": self.locale}


@dataclass(slots=True)
class CallRequest:
    """A goal-driven phone task. CALL-E plans and adapts inside the call;
    PHONEOPS decides what the answer changes about the mission."""

    task: str
    recipient: Recipient
    result_schema: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    webhook_url: str | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task": self.task,
            "recipients": [self.recipient.as_payload()],
            # One operation is one carrier, so the call-level result and the
            # per-recipient result are the same answers. Asking for both means
            # we read whichever CALL-E populates.
            "result_schema": self.result_schema,
            "recipient_result_schema": self.result_schema,
            "metadata": self.metadata,
        }
        if self.webhook_url:
            payload["webhook_url"] = self.webhook_url
        return payload


@dataclass(slots=True)
class CallState:
    call_id: str
    status: str
    outcome: CallOutcome | None = None
    structured_result: dict[str, Any] = field(default_factory=dict)
    result_validation: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    transcript_excerpt: str = ""
    duration_seconds: float | None = None
    error: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class CalleClient(Protocol):
    mode: str

    async def create_call(self, request: CallRequest) -> CallState: ...

    async def get_call(self, call_id: str) -> CallState: ...

    async def list_events(self, call_id: str) -> list[dict[str, Any]]: ...

    async def aclose(self) -> None: ...


def normalise_outcome(status: str | None, structured: dict[str, Any] | None = None) -> CallOutcome:
    raw = (status or "").lower()
    mapping = {
        "completed": CallOutcome.COMPLETED,
        "succeeded": CallOutcome.COMPLETED,
        "success": CallOutcome.COMPLETED,
        "no_answer": CallOutcome.NO_ANSWER,
        "noanswer": CallOutcome.NO_ANSWER,
        "unanswered": CallOutcome.NO_ANSWER,
        "busy": CallOutcome.BUSY,
        "voicemail": CallOutcome.VOICEMAIL,
        "cancelled": CallOutcome.CANCELLED,
        "canceled": CallOutcome.CANCELLED,
        "failed": CallOutcome.FAILED,
        "error": CallOutcome.FAILED,
    }
    if raw in mapping:
        return mapping[raw]
    return CallOutcome.COMPLETED if structured else CallOutcome.FAILED
