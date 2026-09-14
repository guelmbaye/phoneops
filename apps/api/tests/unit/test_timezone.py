"""The displayed cutoff must never contradict the constraint it comes from.

A cutoff is a wall clock ("17:30") belonging to the operation, while phone
answers and constraint values are wall-clock strings with no offset. If the
cutoff is stored as 17:30 UTC and rendered in the operator's zone, a control
room one hour east shows "cutoff 18:30" directly above "must be before 17:30" —
and the whole 18:00-violates-17:30 argument collapses.
"""

from __future__ import annotations

from datetime import UTC
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from app.config import settings
from app.services import timeutil
from app.services.timeutil import today_at


@pytest.mark.parametrize("zone", ["UTC", "Africa/Casablanca", "America/New_York", "Asia/Tokyo"])
def test_the_cutoff_keeps_its_wall_clock_in_the_mission_timezone(zone, monkeypatch):
    monkeypatch.setattr(settings, "MISSION_TIMEZONE", zone)

    cutoff = today_at("17:30")

    assert cutoff.tzinfo is not None, "a naive cutoff cannot be compared safely"
    assert cutoff.astimezone(ZoneInfo(zone)).strftime("%H:%M") == "17:30"


def test_the_cutoff_is_stored_as_an_absolute_instant(monkeypatch):
    monkeypatch.setattr(settings, "MISSION_TIMEZONE", "Africa/Casablanca")

    cutoff = today_at("17:30")

    # Casablanca is UTC+1, so the same instant is 16:30 UTC. Storing 17:30 UTC
    # instead is the bug this pins.
    assert cutoff.astimezone(UTC).strftime("%H:%M") == "16:30"


def test_an_unknown_zone_falls_back_to_utc_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(settings, "MISSION_TIMEZONE", "Mars/Olympus_Mons")

    assert today_at("17:30").astimezone(UTC).strftime("%H:%M") == "17:30"


def test_it_still_works_on_a_machine_with_no_zone_database(monkeypatch):
    """Windows ships no tz database, and neither do slim Linux images.

    There, every ZoneInfo lookup raises — ZoneInfo("UTC") included. A fallback
    that re-enters ZoneInfo raises the error it exists to absorb, and the API
    500s on the first mission it creates.
    """

    def no_database(_key):
        raise ZoneInfoNotFoundError("No time zone found with key")

    monkeypatch.setattr(timeutil, "ZoneInfo", no_database)
    monkeypatch.setattr(settings, "MISSION_TIMEZONE", "Africa/Casablanca")

    # Must not raise, and must still produce a usable aware instant.
    cutoff = timeutil.today_at("17:30")

    assert cutoff.tzinfo is not None
    assert cutoff.astimezone(UTC).strftime("%H:%M") == "17:30"


@pytest.mark.parametrize("zone", ["UTC", "Africa/Casablanca", "Asia/Tokyo"])
def test_a_stored_instant_renders_back_as_the_same_wall_clock(zone, monkeypatch):
    """`wall_clock` is the inverse of `today_at`.

    It is what turns the cutoff back into the constraint every carrier is
    measured against. Formatting the UTC instant directly yields 16:30 for a
    17:30 Casablanca cutoff, so a viable 16:45 pickup reads as a violation.
    """
    monkeypatch.setattr(settings, "MISSION_TIMEZONE", zone)

    assert timeutil.wall_clock(timeutil.today_at("17:30")) == "17:30"


@pytest.mark.parametrize("zone", ["UTC", "Africa/Casablanca", "Asia/Tokyo"])
def test_a_phone_answer_is_anchored_on_the_operation_clock(zone, monkeypatch):
    """ "16:45" from a carrier means 16:45 where the operation runs.

    Anchoring it on the stored instant's UTC day and offset turns a 45-minute
    margin into a 15-minute overrun outside UTC.
    """
    monkeypatch.setattr(settings, "MISSION_TIMEZONE", zone)
    cutoff = timeutil.today_at("17:30")

    pickup = timeutil.combine_cutoff(cutoff, timeutil.parse_time("16:45"))

    assert (cutoff - pickup).total_seconds() == 45 * 60
