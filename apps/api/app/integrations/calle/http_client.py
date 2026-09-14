"""CALL-E Developer API client (Phase 1 surface).

    POST /v1/calls                 create a call
    GET  /v1/calls/{call_id}       read call state and results
    GET  /v1/calls/{call_id}/events developer-facing call events
    POST {our webhook}             terminal call result callback

Auth: `Authorization: Bearer $CALLE_API_KEY`, base URL from `CALLE_BASE_URL`.
Idempotency-Key is always sent so a retried recovery operation never places a
second real phone call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.config import settings
from app.domain.errors import CalleError
from app.integrations.calle.base import CallRequest, CallState, normalise_outcome
from app.integrations.calle.regions import (
    is_supported,
    region_of,
    unsupported_region_message,
)
from app.logging_config import get_logger

log = get_logger("calle.http")

_RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}
#: CALL-E refused the request itself — credentials, schema, or an unusable
#: number. No call was placed, and retrying the same payload cannot help.
_REJECTED = {400, 401, 403, 404, 422}


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class HttpCalleClient:
    mode = "http"

    def __init__(self, *, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = (base_url or settings.CALLE_BASE_URL).rstrip("/")
        self.api_key = api_key or settings.CALLE_API_KEY
        if not self.api_key:
            raise CalleError("CALLE_API_KEY is not configured (CALLE_MODE=http)")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=settings.CALLE_TIMEOUT_SECONDS,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": (
                    f"{settings.CALLE_INTEGRATION}/{settings.CALLE_INTEGRATION_VERSION}"
                ),
                "X-Calle-Source": settings.CALLE_SOURCE,
                "X-Calle-Integration": settings.CALLE_INTEGRATION,
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ api
    async def create_call(self, request: CallRequest) -> CallState:
        # Refuse before dialling. An unsupported destination is accepted by the
        # API with `201 Created` and only fails per attempt, so the charge lands
        # and the operator is told the carrier did not answer.
        # The number decides, not the declared region: a candidate can say
        # `region: "US"` and carry a +212 number, and CALL-E routes on the
        # number. Checking the label validated nothing.
        reachable = region_of(request.recipient.phone)
        if reachable is None or not is_supported(request.recipient.region):
            raise CalleError(
                unsupported_region_message(
                    reachable or request.recipient.region, request.recipient.phone
                ),
                transient=False,
                details={
                    "rejected": True,
                    "status": 0,
                    "unsupported_destination": True,
                    "region": request.recipient.region,
                    "phone": request.recipient.phone,
                },
            )

        headers: dict[str, str] = {}
        if request.idempotency_key:
            headers["Idempotency-Key"] = request.idempotency_key

        payload = request.as_payload()
        if not payload.get("webhook_url") and settings.CALLE_WEBHOOK_URL:
            payload["webhook_url"] = settings.CALLE_WEBHOOK_URL

        log.info(
            "calle.create_call",
            target=request.metadata.get("target"),
            mission_id=request.metadata.get("mission_id"),
            operation_id=request.metadata.get("operation_id"),
        )
        data = await self._request("POST", "/v1/calls", json=payload, headers=headers)
        return self._to_state(data)

    async def get_call(self, call_id: str) -> CallState:
        data = await self._request("GET", f"/v1/calls/{call_id}")
        return self._to_state(data)

    async def list_events(self, call_id: str) -> list[dict[str, Any]]:
        data = await self._request("GET", f"/v1/calls/{call_id}/events")
        events = data.get("events", data.get("data", []))
        return events if isinstance(events, list) else []

    # ------------------------------------------------------------ internals
    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise CalleError(f"CALL-E timeout on {method} {path}", transient=True) from exc
        except httpx.HTTPError as exc:
            raise CalleError(f"CALL-E transport error: {exc}", transient=True) from exc

        if response.status_code >= 400:
            body = response.text[:800]
            rejected = response.status_code in _REJECTED
            # The body is the only thing that says which field CALL-E refused.
            # Capturing it in `details` and never printing it left an operator
            # with "could not reach Carrier B" and nothing to act on.
            log.error(
                "calle.request_rejected" if rejected else "calle.request_failed",
                status=response.status_code,
                path=path,
                body=body,
            )
            raise CalleError(
                f"CALL-E {method} {path} -> {response.status_code}: {body}",
                transient=response.status_code in _RETRYABLE,
                details={"body": body, "status": response.status_code, "rejected": rejected},
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise CalleError("CALL-E returned a non-JSON body") from exc
        return body if isinstance(body, dict) else {"data": body}

    @staticmethod
    def _to_state(data: dict[str, Any]) -> CallState:
        # Tolerate snake_case and camelCase - the beta contract is still moving.
        call = data.get("call", data.get("data", data))
        call_id = str(call.get("id") or call.get("call_id") or "")
        if not call_id:
            raise CalleError(
                "CALL-E response is missing a call id", details={"body": str(data)[:400]}
            )

        status = str(call.get("status") or "queued").lower()

        # Each dial attempt carries `failure_code` and `failure_message`. Not
        # reading them is why six real calls showed as "failed" with no reason
        # — the same blindness as capturing the 422 body and never printing it.
        attempts = call.get("attempts") or []
        failure_code = failure_message = ""
        for attempt in reversed(attempts):
            if isinstance(attempt, dict) and (
                attempt.get("failure_code") or attempt.get("failureCode")
            ):
                failure_code = str(attempt.get("failure_code") or attempt.get("failureCode"))
                failure_message = str(
                    attempt.get("failure_message") or attempt.get("failureMessage") or ""
                )
                break
        structured = call.get("structured_result") or call.get("structuredResult") or {}
        if not structured:
            # A single-recipient call carries its answers per recipient.
            recipients = call.get("recipients") or []
            if recipients and isinstance(recipients[0], dict):
                structured = (
                    recipients[0].get("structured_result")
                    or recipients[0].get("structuredResult")
                    or {}
                )
        validation = call.get("result_validation") or call.get("resultValidation") or {}
        transcript = call.get("transcript") or ""
        if isinstance(transcript, list):  # some payloads return turn objects
            transcript = " ".join(
                str(turn.get("text", "")) for turn in transcript if isinstance(turn, dict)
            )

        return CallState(
            call_id=call_id,
            status=status,
            # `failed` covers busy, unanswered and a dead number alike; the
            # attempt's own code is what distinguishes them, and what decides
            # whether PHONEOPS retries or moves on.
            outcome=normalise_outcome(failure_code or status, structured),
            structured_result=structured if isinstance(structured, dict) else {},
            result_validation=validation if isinstance(validation, dict) else {},
            summary=str(call.get("summary") or ""),
            # Data minimisation (DOCUMENT 09 §27): keep a short excerpt, not the call.
            transcript_excerpt=str(transcript)[:600],
            duration_seconds=call.get("duration_seconds") or call.get("durationSeconds"),
            error=(
                f"{failure_code}: {failure_message}".strip(": ")
                or call.get("error")
                or call.get("error_message")
            ),
            started_at=_parse_dt(call.get("started_at") or call.get("startedAt")),
            ended_at=_parse_dt(call.get("ended_at") or call.get("endedAt")),
            raw=call if isinstance(call, dict) else {},
        )
