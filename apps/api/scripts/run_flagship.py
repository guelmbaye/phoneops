"""Run the flagship recovery mission and print the full causal chain."""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./flagship.db")
os.environ.setdefault("DEMO_CALL_LATENCY_SECONDS", "0")

from app.db.session import init_models, session_scope  # noqa: E402
from app.engine import director  # noqa: E402
from app.seeds.flagship import build_flagship_payload  # noqa: E402
from app.services.mission_service import create_exception_and_mission, get_mission  # noqa: E402
from app.services.view import build_recovery_view, explain  # noqa: E402

BAR = "═" * 78


async def main() -> int:
    await init_models()
    async with session_scope() as s:
        mission = await create_exception_and_mission(s, build_flagship_payload())
        mission_id = mission.id
        await director.start_mission(s, mission_id)

    async with session_scope() as s:
        view = await build_recovery_view(s, await get_mission(s, mission_id))
        why = await explain(s, mission_id)

    print(f"{BAR}\n PHONEOPS AI - flagship recovery mission\n{BAR}")
    print(f" Exception : {view.exception.description}")
    print(f" Impact    : {view.impact['consequence']}")
    print(f" Objective : {view.mission.objective}")
    print(f" Cutoff    : {view.mission.deadline.cutoff_at:%H:%M}\n")

    print(" RECOVERY TIMELINE")
    for event in view.timeline:
        if event.strategic:
            print(f"   {event.sequence:>3}  {event.type:<24} {event.title}")

    print("\n WHY DID PHONEOPS CHANGE THE PLAN?")
    for entry in why["replans"]:
        print(f"   what happened  : {entry['what_happened']}")
        print(f"   why it matters : {entry['why_it_matters']}")
        print(f"   what changed   : {entry['what_changed']}")
        print(f"   what's next    : {entry['what_happens_next']}")
        print(
            f"   source         : {entry['source']['display_ref']} "
            f"({entry['source']['provider_mode']}) / {entry['source']['evidence_id']}"
        )

    if view.outcome:
        margin = (view.outcome.margin_seconds or 0) // 60
        print(f"\n OUTCOME: {view.outcome.headline}")
        print(f"   {view.outcome.selected_target} - {view.outcome.facts} ({margin} min margin)")
    print(f"\n METRICS: {view.metrics.model_dump()}\n")
    return 0 if view.mission.status == "recovered" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
