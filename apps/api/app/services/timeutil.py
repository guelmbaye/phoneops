"""Time and value normalisation.

Hard constraints are compared here - deterministically, never by an LLM
(DOCUMENT 09 §9: "The LLM should not decide whether 18:00 <= 17:30").
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings

_TIME_PATTERNS = [
    re.compile(r"^(?P<h>\d{1,2}):(?P<m>\d{2})(?::\d{2})?$"),
    re.compile(r"^(?P<h>\d{1,2})h(?P<m>\d{2})?$", re.I),
    re.compile(r"^(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm|a\.m\.|p\.m\.)$", re.I),
]

_WORD_HOURS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "noon": 12,
    "midnight": 0,
}

#: Hedging language must never become a confirmed fact (DOCUMENT 09 §15).
HEDGE_MARKERS = (
    "probably",
    "maybe",
    "around",
    "roughly",
    "approximately",
    "should be",
    "i think",
    "we'll try",
    "we will try",
    "hopefully",
    "about",
    "somewhere",
    "peut-etre",
    "environ",
    "vers",
)


#: Clock seam. Production leaves it unset; tests and replay pin it so behaviour
#: that depends on "now" is reproducible instead of changing at 16:45.
_clock: datetime | None = None


def set_clock(moment: datetime | None) -> None:
    global _clock
    _clock = moment


def utcnow() -> datetime:
    return _clock if _clock is not None else datetime.now(UTC)


def as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_time(value: object) -> time | None:
    """Parse '17:30', '5:30 pm', '6 PM', '18h', 'six' -> datetime.time."""
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.timetz().replace(tzinfo=None)
    if value is None:
        return None

    raw = str(value).strip().lower().replace(".", ".").replace("\u00a0", " ")
    if not raw:
        return None

    ampm_free = raw.replace("o'clock", "").strip()
    for word, hour in _WORD_HOURS.items():
        if ampm_free.startswith(word):
            rest = ampm_free[len(word) :].strip()
            h = hour
            if "pm" in rest and h < 12:
                h += 12
            return time(hour=h % 24, minute=0)

    compact = ampm_free.replace(" ", "")
    for pattern in _TIME_PATTERNS:
        m = pattern.match(compact)
        if not m:
            continue
        h = int(m.group("h"))
        m_ = int(m.group("m") or 0)
        ap = (m.groupdict().get("ap") or "").replace(".", "")
        if ap.startswith("p") and h < 12:
            h += 12
        if ap.startswith("a") and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= m_ <= 59:
            return time(hour=h, minute=m_)
    return None


def format_time(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


_THOUSANDS = re.compile(r"^-?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def parse_number(value: object) -> float | None:
    """Handles '5,000' (thousands), '2.5' and '3,5' (decimal comma), '+35%'."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if value is None:
        return None

    raw = str(value).strip().replace(" ", "").replace("\u00a0", "")
    m = re.search(r"-?\d[\d.,]*", raw)
    if not m:
        return None

    token = m.group().rstrip(".,")
    if _THOUSANDS.match(token):
        token = token.replace(",", "")
    elif token.count(",") == 1 and token.count(".") == 0:
        token = token.replace(",", ".")  # decimal comma
    else:
        token = token.replace(",", "")

    try:
        return float(token)
    except ValueError:
        return None


def parse_boolean(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw in {"true", "yes", "y", "1", "confirmed", "available", "ok", "oui", "full"}:
        return True
    if raw in {"false", "no", "n", "0", "unavailable", "declined", "non", "none"}:
        return False
    return None


def is_hedged(raw: str | None) -> bool:
    if not raw:
        return False
    lowered = raw.lower()
    return any(marker in lowered for marker in HEDGE_MARKERS)


def mission_timezone() -> tzinfo:
    """The zone a mission's wall-clock times are expressed in.

    Windows ships no system zone database, so without the `tzdata` package
    every ZoneInfo lookup fails - including ZoneInfo("UTC"). The fallback must
    therefore not re-enter ZoneInfo, or it raises the very error it exists to
    absorb and the whole API 500s on the first mission.
    """
    try:
        return ZoneInfo(settings.MISSION_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError, ImportError, KeyError):
        return UTC


def wall_clock(moment: datetime) -> str:
    """Render a stored instant back as the operation's wall clock.

    The inverse of `today_at`. Formatting a UTC instant directly gives 16:30 for
    a 17:30 Casablanca cutoff, which then becomes the constraint every carrier
    is measured against - so a viable 16:45 pickup reads as a violation and the
    mission escalates instead of recovering.
    """
    return as_aware(moment).astimezone(mission_timezone()).strftime("%H:%M")


def combine_cutoff(cutoff: datetime, clock: time) -> datetime:
    """Anchor an 'HH:MM' answer on the operational day the window belongs to.

    A carrier saying "16:45" means 16:45 on the operation's clock, and for a
    same-day cutoff that is the cutoff's own date. It is not for a window that
    crosses midnight: with a 02:00 cutoff, "23:00" means tonight, three hours
    before it — anchoring on the cutoff's date placed it twenty-one hours after
    and turned a viable answer into a violation.

    The answer is anchored on whichever adjacent day lands it nearest the
    cutoff, which is the same as the cutoff's date whenever the window does not
    straddle midnight.
    """
    tz = mission_timezone()
    local_cutoff = as_aware(cutoff).astimezone(tz)
    candidates = [
        datetime.combine(local_cutoff.date() + timedelta(days=offset), clock, tzinfo=tz)
        for offset in (-1, 0, 1)
    ]
    return min(candidates, key=lambda moment: abs(moment - local_cutoff))


def today_at(clock: str, *, base: date | None = None) -> datetime:
    """Turn a wall-clock string into an instant.

    "17:30" means 17:30 where the operation runs. Combining it with UTC and
    then rendering it in the operator's browser is what makes a 17:30 cutoff
    display as 18:30 one timezone east.
    """
    tz = mission_timezone()
    parsed = parse_time(clock) or time(0, 0)
    day = base or utcnow().astimezone(tz).date()
    return datetime.combine(day, parsed, tzinfo=tz).astimezone(UTC)


def remaining_seconds(deadline: datetime, *, now: datetime | None = None) -> int:
    delta = as_aware(deadline) - (now or utcnow())
    return int(delta.total_seconds())


def humanize_countdown(seconds: int) -> str:
    seconds = max(seconds, 0)
    return str(timedelta(seconds=seconds))
