"""The wire format sent to CALL-E.

Nothing asserted this, so a payload that every real call rejected with 422
passed the whole suite: the mock client accepts any shape, and the only
component that would have objected is the provider itself.

Checked against Developer API 0.7.0 — `CreateCallRequest` is
`additionalProperties: false` with exactly six properties, so an unknown key
fails the request before any number is dialled.
"""

from __future__ import annotations

import pytest

from app.domain.enums import CallOutcome
from app.integrations.calle.base import CallRequest, Recipient
from app.integrations.calle.result_schema import build_result_schema

#: CreateCallRequest, Developer API 0.7.0.
ALLOWED = {
    "task",
    "recipients",
    "result_schema",
    "recipient_result_schema",
    "metadata",
    "webhook_url",
}


def _request() -> CallRequest:
    return CallRequest(
        task="Call Carrier B and ask for the earliest pickup time.",
        recipient=Recipient(phone="+212600000000", region="MA", locale="fr-MA"),
        result_schema=build_result_schema(["available", "pickup_time"]),
        metadata={"mission_id": "m1", "operation_id": "op1"},
        webhook_url="https://example.com/api/webhooks/calle",
    )


def test_the_payload_uses_only_fields_the_api_accepts():
    payload = _request().as_payload()

    unknown = set(payload) - ALLOWED
    assert unknown == set(), f"additionalProperties: false will reject {unknown}"


def test_recipients_is_a_list_and_carries_phones_as_a_list():
    payload = _request().as_payload()

    assert "recipient" not in payload, "the field is plural; the singular is rejected"
    assert isinstance(payload["recipients"], list)
    assert payload["recipients"][0]["phones"] == ["+212600000000"]
    assert payload["recipients"][0]["region"] == "MA"
    assert payload["recipients"][0]["locale"] == "fr-MA"


def test_the_facts_are_asked_for_at_both_levels():
    """One operation is one carrier, so the call result and the recipient
    result are the same answers. Asking for both means we read whichever
    CALL-E populates."""
    payload = _request().as_payload()

    assert payload["result_schema"] == payload["recipient_result_schema"]
    assert "pickup_time" in payload["result_schema"]["properties"]


@pytest.mark.parametrize("field", sorted(ALLOWED - {"webhook_url"}))
def test_every_required_field_is_present(field):
    assert field in _request().as_payload()


# --- schema features CALL-E actually supports -------------------------------
#
# `["boolean", "null"]` was rejected with `result_schema_invalid` on every live
# call. Supported: type, properties, required, enum, nested objects, simple
# array.items, description, additionalProperties: false.

UNSUPPORTED_KEYWORDS = {"$ref", "oneOf", "anyOf", "allOf", "format", "pattern"}


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def test_no_field_declares_a_union_type():
    schema = build_result_schema(["available", "pickup_time", "capacity_ok"])

    unions = [
        (name, prop["type"])
        for name, prop in schema["properties"].items()
        if isinstance(prop.get("type"), list)
    ]
    assert unions == [], f"union types are rejected upstream: {unions}"


def test_the_schema_uses_no_unsupported_keyword():
    schema = build_result_schema(["available", "pickup_time", "quantity"])

    found = {k for node in _walk(schema) for k in node if k in UNSUPPORTED_KEYWORDS}
    assert found == set(), f"unsupported by the extraction layer: {found}"


def test_a_yes_no_fact_is_an_enum_with_an_unknown_member():
    """Their guidance, and ours: an unanswered question must never read as "no"."""
    schema = build_result_schema(["available"])

    assert schema["properties"]["available"]["enum"] == ["yes", "no", "unknown"]


def test_the_confirmation_flag_can_say_approximate():
    schema = build_result_schema(["pickup_time"])

    assert schema["properties"]["pickup_time_confirmed"]["enum"] == [
        "confirmed",
        "approximate",
        "unknown",
    ]


def test_every_field_carries_a_description_for_the_extraction_model():
    schema = build_result_schema(["available", "pickup_time", "capacity_ok"])

    undescribed = [
        name
        for name, prop in schema["properties"].items()
        if not prop.get("description") and not prop.get("enum")
    ]
    assert undescribed == []


# --- terminal state ---------------------------------------------------------


def _state(raw):
    from app.integrations.calle.http_client import HttpCalleClient

    return HttpCalleClient._to_state(raw)


def test_the_attempt_failure_code_decides_the_outcome():
    """Six real calls reported "failed" with no reason, because the code and
    message live on the attempt and nothing read them."""
    state = _state(
        {
            "id": "call_1",
            "status": "failed",
            "attempts": [
                {"failure_code": "no_answer", "failure_message": "Recipient did not answer."}
            ],
        }
    )

    assert state.outcome == CallOutcome.NO_ANSWER
    assert "Recipient did not answer" in state.error


def test_a_busy_line_is_not_the_same_as_a_dead_number():
    busy = _state({"id": "c", "status": "failed", "attempts": [{"failure_code": "busy"}]})
    dead = _state({"id": "c", "status": "failed", "attempts": [{"failure_code": "invalid_number"}]})

    assert busy.outcome == CallOutcome.BUSY
    assert dead.outcome == CallOutcome.FAILED


def test_a_single_recipient_result_is_read_from_the_recipient():
    state = _state(
        {
            "id": "call_2",
            "status": "completed",
            "recipients": [{"structured_result": {"available": "yes"}}],
        }
    )

    assert state.structured_result == {"available": "yes"}


# --- destinations -----------------------------------------------------------


def test_morocco_is_not_a_supported_destination():
    """Six real calls to +212 were dialled, charged and reported as "failed"
    with no reason. The request is well-formed, so the API returns 201 and the
    failure only surfaces per attempt — the one configuration error the payload
    cannot reveal."""
    from app.integrations.calle.regions import is_supported

    assert not is_supported("MA")
    assert is_supported("FR")
    assert is_supported("TN")


def test_the_message_names_the_destination_and_what_to_do():
    """The declared region is a label; the number is where the call goes."""
    from app.integrations.calle.regions import unsupported_region_message

    message = unsupported_region_message(None, "+212600000000")

    assert "+212" in message and "+212600000000" in message
    assert "CALLE_DEFAULT_REGION" in message
    assert "FR" in message, "an operator needs somewhere to go, not just a refusal"


def test_the_destination_comes_from_the_number_not_the_declared_region():
    """A candidate can say `region: "US"` and carry a +212 number, and CALL-E
    routes on the number. Checking the label validated nothing, which is why
    six real calls were placed to a country it does not serve."""
    from app.integrations.calle.regions import can_dial, region_of

    assert region_of("+33612345678") == "FR"
    assert region_of("+21612345678") == "TN"
    assert region_of("+212600000000") is None
    assert not can_dial("+212600000000")


@pytest.mark.asyncio
async def test_an_unsupported_destination_is_refused_before_it_is_dialled():
    from app.domain.errors import CalleError
    from app.integrations.calle.http_client import HttpCalleClient

    client = HttpCalleClient(base_url="https://example.invalid", api_key="k")
    request = _request()
    request.recipient.region = "MA"

    with pytest.raises(CalleError) as raised:
        await client.create_call(request)

    # Classified as a rejection, so the mission escalates instead of walking the
    # candidate list and charging for every one of them.
    assert raised.value.details["rejected"] is True
    await client.aclose()
