"""Flagship scenario - Shipment #4821, carrier cancellation.

The scenario is controlled (fixed shipment, known candidates, deterministic
constraints); the *result processing* is not. Which carrier gets called second
is decided by the constraint engine from whatever the phone actually returns.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from app.config import settings
from app.domain.enums import ExceptionType, Severity
from app.domain.schemas import CandidateInput, ExceptionCreate
from app.integrations.calle.mock_client import DEFAULT_SCRIPTS
from app.services.timeutil import mission_timezone, remaining_seconds, today_at, utcnow

FLAGSHIP = {
    "entity_ref": "Shipment #4821",
    "cutoff_clock": "17:30",
    "cancelled_carrier": "Carrier A",
}


def _phone(env_key: str, default: str) -> str:
    return os.getenv(env_key, default)


def _next_cutoff(clock: str) -> datetime:
    """The next day on which the whole scenario is still playable.

    Rolling only when the 17:30 cutoff has passed is not enough: from 16:45 the
    winning carrier's own pickup time is already behind us, the engine correctly
    refuses a collection that cannot happen, and the demo escalates. The
    scenario needs its earliest scripted time still ahead, not just its
    deadline.

    Only the seed moves the date. The engine keeps treating a past deadline as
    past, and a past pickup as impossible.
    """
    scripted = [
        script["pickup_time"] for script in DEFAULT_SCRIPTS.values() if script.get("pickup_time")
    ]
    earliest = min([clock, *scripted])
    if remaining_seconds(today_at(earliest)) > 0:
        return today_at(clock)

    tomorrow = (utcnow().astimezone(mission_timezone()) + timedelta(days=1)).date()
    return today_at(clock, base=tomorrow)


def build_flagship_payload(*, demo: bool = True, autostart: bool = False) -> ExceptionCreate:
    cutoff = _next_cutoff(FLAGSHIP["cutoff_clock"])
    # The wording has to follow the date. Once the scenario rolls forward,
    # "today's pickup" and "tonight's departure" are simply false, and a judge
    # reads a 23-hour countdown under a sentence claiming tonight.
    same_day = (
        cutoff.astimezone(mission_timezone()).date()
        == utcnow().astimezone(mission_timezone()).date()
    )
    when, evening = ("today", "Tonight") if same_day else ("tomorrow", "Tomorrow evening")

    return ExceptionCreate(
        type=ExceptionType.CARRIER_CANCELLATION,
        entity_ref=FLAGSHIP["entity_ref"],
        description=(
            f"{FLAGSHIP['cancelled_carrier']} cancelled {when}'s pickup for "
            f"{FLAGSHIP['entity_ref']}."
        ),
        severity=Severity.CRITICAL,
        threatened_outcome=f"{evening}'s shipment departure",
        consequence=(
            f"Shipment may miss {evening.lower()}'s departure, breaching the delivery "
            "SLA and delaying every downstream leg."
        ),
        recovery_cutoff=cutoff,
        cutoff_clock=FLAGSHIP["cutoff_clock"],
        source="manual",
        demo=demo,
        autostart=autostart,
        payload={
            "cancelled_carrier": FLAGSHIP["cancelled_carrier"],
            "warehouse": "DC-01",
            "departure": when,
        },
        candidates=[
            CandidateInput(
                name="Carrier B",
                phone=settings.DEMO_CARRIER_B_PHONE,
                rank=10,
                meta={"role": "carrier", "tier": "primary_backup"},
            ),
            CandidateInput(
                name="Carrier C",
                phone=settings.DEMO_CARRIER_C_PHONE,
                rank=20,
                meta={"role": "carrier", "tier": "secondary_backup"},
            ),
            CandidateInput(
                name="Carrier D",
                phone=settings.DEMO_CARRIER_D_PHONE,
                rank=30,
                meta={"role": "carrier", "tier": "spot_market"},
            ),
        ],
    )
