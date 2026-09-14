"""Builds the JSON Schema handed to CALL-E's `result_schema`.

This is what turns a conversation into machine-readable evidence: CALL-E is
asked for exactly the facts the mission is missing, nothing more. Each fact is
paired with a `*_confirmed` flag so hedged answers ("probably around 5:30")
cannot be silently promoted into a satisfied hard constraint.
"""

from __future__ import annotations

from typing import Any

from app.domain.enums import ValueType

FACT_TYPES: dict[str, ValueType] = {
    "available": ValueType.BOOLEAN,
    "capacity_ok": ValueType.BOOLEAN,
    "pickup_time": ValueType.TIME,
    "delivery_time": ValueType.TIME,
    "arrival_time": ValueType.TIME,
    "quantity": ValueType.NUMBER,
    "cost_increase_pct": ValueType.NUMBER,
}

#: CALL-E supports `type`, `properties`, `required`, `enum`, nested objects,
#: simple `array.items`, `description` and `additionalProperties: false`. Union
#: types are not supported: `["boolean", "null"]` is rejected with
#: `result_schema_invalid` before any number is dialled, which is how every call
#: failed on the first live run.
#:
#: Their guidance is to prefer a string enum with an explicit `unknown` member
#: over a boolean for anything the call may not settle — which suits this
#: product exactly, since an unanswered question must never read as "no".
_UNKNOWN = "unknown"

_JSON_TYPE = {
    ValueType.BOOLEAN: {"type": "string", "enum": ["yes", "no", _UNKNOWN]},
    ValueType.NUMBER: {
        "type": "string",
        "description": f"A plain number, or '{_UNKNOWN}' if the contact did not say.",
    },
    ValueType.TIME: {
        "type": "string",
        "description": (f"24h clock time as HH:MM, or '{_UNKNOWN}' if no time was given."),
    },
    ValueType.STRING: {"type": "string"},
    ValueType.DATETIME: {"type": "string"},
}

_DESCRIPTIONS = {
    "available": (
        "'yes' only if the contact explicitly confirms they can take the job. "
        "'unknown' if they did not say."
    ),
    "capacity_ok": (
        "'yes' only if the contact confirms they can handle the full volume. "
        "'unknown' if they did not say."
    ),
    "pickup_time": "Earliest pickup time the contact commits to, as HH:MM (24h).",
    "delivery_time": "Earliest delivery time the contact commits to, as HH:MM (24h).",
    "arrival_time": "Earliest on-site arrival time the contact commits to, as HH:MM (24h).",
    "quantity": "Number of units the contact can supply.",
    "cost_increase_pct": (
        "Extra cost versus the standard rate, in percent. '0' if none, "
        "'unknown' if not discussed."
    ),
}


def value_type_for(fact: str) -> ValueType:
    return FACT_TYPES.get(fact, ValueType.STRING)


#: Asked on every call regardless of the mission's constraints. Cost is never a
#: constraint, but it is what moves a viable path behind human approval, so the
#: policy layer must always receive it.
ALWAYS_ASKED: tuple[str, ...] = ("cost_increase_pct",)


def facts_asked(facts: list[str]) -> list[str]:
    """The complete list CALL-E is asked for, constraints plus the standing ones.

    The operation must record this rather than the constraint-derived list, or
    the API reports three requested facts while four come back from the same
    call, and the provenance the product rests on stops adding up.
    """
    asked = list(facts)
    asked.extend(f for f in ALWAYS_ASKED if f not in asked)
    return asked


def build_result_schema(facts: list[str]) -> dict[str, Any]:
    """Strict schema: additionalProperties disabled, every fact nullable."""
    properties: dict[str, Any] = {}
    required: list[str] = []

    for fact in facts:
        base = dict(_JSON_TYPE[value_type_for(fact)])
        if fact in _DESCRIPTIONS:
            base["description"] = _DESCRIPTIONS[fact]
        properties[fact] = base
        required.append(fact)

        # Three states, not two. "approximate" is what a hedge actually is, and
        # naming it lets the extraction model choose it instead of guessing at a
        # boolean — which is the difference between a clarification call and a
        # cutoff wrongly declared satisfied.
        properties[f"{fact}_confirmed"] = {
            "type": "string",
            "enum": ["confirmed", "approximate", _UNKNOWN],
            "description": (
                f"How firmly '{fact}' was stated. 'confirmed' for a commitment, "
                "'approximate' for hedged answers such as 'probably' or 'around', "
                f"'{_UNKNOWN}' if it was not stated at all."
            ),
        }

    for fact in ALWAYS_ASKED:
        if fact not in properties:
            base = dict(_JSON_TYPE[value_type_for(fact)])
            base["description"] = _DESCRIPTIONS[fact]
            properties[fact] = base
            required.append(fact)

    properties["notes"] = {
        "type": "string",
        "description": "One short sentence capturing anything that changes feasibility.",
    }

    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }
