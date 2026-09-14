"""Human wording for anything an operator reads.

The audit trail keeps its precision in structured fields — `ConstraintEvaluation.
explanation` still records "Required pickup_time <= 17:30; observed 18:00 ->
VIOLATED.", and every event carries the constraint, operator, required and
observed values in its payload. What changes here is the *title*: the recovery
timeline is read by people, and a panel that mixes `available = True` with a
sentence in English has two registers in one place.

One module, so the phrasing cannot drift between the timeline, the API and
Mission Control — the UI reads `short_label` from here rather than keeping its
own copy of the same map.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.timeutil import as_aware, mission_timezone, utcnow

#: Short noun phrases for the requirements the flagship templates create. The
#: fallback derives one from the key, so an unknown constraint still reads.
_REQUIREMENTS = {
    "pickup_time": "the pickup cutoff",
    "arrival_time": "the arrival deadline",
    "capacity_ok": "the capacity requirement",
    "available": "the availability requirement",
    "quantity": "the required quantity",
    "cost_increase_pct": "the cost ceiling",
}

_BOUNDS = {
    "lte": "{value} or earlier",
    "gte": "{value} or more",
    "lt": "before {value}",
    "gt": "after {value}",
    "eq": "{value}",
    "neq": "anything but {value}",
    "in": "one of {value}",
}


def requirement_phrase(key: str) -> str:
    """ "pickup_time" -> "the pickup cutoff"."""
    return _REQUIREMENTS.get(key, f"the {key.replace('_', ' ')} requirement")


#: Reading "Carrier B available: yes" aloud shows why a raw key is not a label.
#: The fallback strips units and underscores for anything unmapped.
_FACTS = {
    "available": "availability",
    "capacity_ok": "capacity",
    "pickup_time": "pickup time",
    "arrival_time": "arrival time",
    "cost_increase_pct": "cost increase",
}


def fact_label(key: str) -> str:
    """ "cost_increase_pct" -> "cost increase". The unit belongs on the value."""
    if key in _FACTS:
        return _FACTS[key]
    return key.removesuffix("_pct").removesuffix("_percent").replace("_", " ")


def fact_value(key: str, value: Any) -> str:
    """Render an answer the way it was given, not the way Python stores it."""
    if value is None:
        return "not stated"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if key.endswith(("_pct", "_percent")):
        number = float(value)
        return f"{number:g}%"
    return str(value)


def requirement_bound(operator: str, value: Any) -> str:
    """ "lte", "17:30" -> "17:30 or earlier"."""
    return _BOUNDS.get(operator, f"{operator} {{value}}").format(value=fact_value("", value))


def evidence_title(subject: str, key: str, value: Any) -> str:
    """Self-contained: the timeline is scanned out of order, and two carriers
    answering the same question must not be told apart by position alone."""
    return f"{subject} {fact_label(key)}: {fact_value(key, value)}"


def evaluation_title(
    subject: str,
    *,
    key: str,
    operator: str,
    required: Any,
    observed: Any,
    satisfied: bool,
) -> str:
    verb = "meets" if satisfied else "cannot meet"
    head = f"{subject} {verb} {requirement_phrase(key)}"

    seen = fact_value(key, observed)
    bound = requirement_bound(operator, required)
    # "meets the capacity requirement — yes, required yes" says nothing twice.
    # The comparison earns its place only when the two readings differ.
    if seen == bound:
        return head
    return f"{head} — {seen}, required {bound}"


def day_phrase(deadline: datetime) -> str:
    """ "today", "tomorrow", or a weekday — for the deadline's own day.

    Used in the questions CALL-E asks. Asking "can you collect this today?"
    about a window that closes tomorrow gets an accurate answer to the wrong
    question, from a real person on a real call.
    """
    tz = mission_timezone()
    target = as_aware(deadline).astimezone(tz).date()
    today = utcnow().astimezone(tz).date()
    delta = (target - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    return target.strftime("%A")
