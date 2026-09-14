from __future__ import annotations

from datetime import time

import pytest

from app.services.timeutil import format_time, is_hedged, parse_boolean, parse_number, parse_time


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("17:30", time(17, 30)),
        ("18:00", time(18, 0)),
        ("6 PM", time(18, 0)),
        ("6:00 pm", time(18, 0)),
        ("5:30 p.m.", time(17, 30)),
        ("16h45", time(16, 45)),
        ("four", time(4, 0)),
        ("noon", time(12, 0)),
        ("12 am", time(0, 0)),
        ("garbage", None),
        (None, None),
    ],
)
def test_parse_time(raw, expected):
    assert parse_time(raw) == expected


def test_format_time():
    assert format_time(parse_time("6 PM")) == "18:00"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2,000", 2000.0),  # thousands separator
        ("5 000", 5000.0),
        ("3,5", 3.5),  # decimal comma
        ("2.5", 2.5),
        (5000, 5000.0),
        ("+35%", 35.0),
        ("none", None),
    ],
)
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize(
    "raw,expected", [("yes", True), ("confirmed", True), ("no", False), ("maybe", None)]
)
def test_parse_boolean(raw, expected):
    assert parse_boolean(raw) is expected


def test_hedging_is_detected():
    assert is_hedged("we can probably be there around 5:30")
    assert is_hedged("it should be fine")
    assert not is_hedged("we will be there at 16:45")
