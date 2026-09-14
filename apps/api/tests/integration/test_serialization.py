"""Every timestamp the API emits must carry an explicit offset.

SQLite has no timezone type, so instants come back from the database naive.
Serialised bare, `new Date("2026-09-06T10:29:05")` is parsed by the browser as
*local* time — so Mission Control renders the timeline shifted by the operator's
UTC offset while the cutoff, the one field that was re-stamped by hand, stays
correct. The two then disagree by an hour on the same screen.

Asserting over the whole serialised payload rather than a list of fields is the
point: a new timestamp field must not be able to slip through.
"""

from __future__ import annotations

import json
import re

import pytest

from app.engine import director
from app.seeds.flagship import build_flagship_payload
from app.services.mission_service import create_exception_and_mission, get_mission
from app.services.view import build_recovery_view

#: ISO-8601 that ends without Z or ±HH:MM.
NAIVE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$")


def _naive_timestamps(node, path="") -> list[str]:
    if isinstance(node, dict):
        return [p for k, v in node.items() for p in _naive_timestamps(v, f"{path}.{k}")]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in _naive_timestamps(v, f"{path}[{i}]")]
    if isinstance(node, str) and NAIVE.match(node):
        return [f"{path} = {node}"]
    return []


@pytest.mark.asyncio
async def test_no_timestamp_leaves_the_api_without_an_offset(session, calle):
    mission = await create_exception_and_mission(session, build_flagship_payload())
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    payload = json.loads(view.model_dump_json())
    naive = _naive_timestamps(payload)

    assert naive == [], f"serialised without a timezone offset: {naive}"


@pytest.mark.asyncio
async def test_the_timeline_and_the_cutoff_agree_on_their_frame(session, calle):
    """Both must be readable as absolute instants, not wall clocks."""
    mission = await create_exception_and_mission(session, build_flagship_payload())
    await director.start_mission(session, mission.id)
    view = await build_recovery_view(session, await get_mission(session, mission.id))

    assert view.mission.deadline.cutoff_at.tzinfo is not None
    assert all(event.created_at.tzinfo is not None for event in view.timeline)
    assert all(item.observed_at.tzinfo is not None for item in view.evidence)
