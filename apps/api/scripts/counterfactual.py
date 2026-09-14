"""Anti-hardcoding proof.

Runs the SAME mission twice. The only difference is what the carrier says on the
phone. If the second call appeared in both runs, PHONEOPS would be a scripted
demo - it does not.

    make counterfactual
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./counterfactual.db")
os.environ.setdefault("DEMO_CALL_LATENCY_SECONDS", "0")
os.environ.setdefault("CALLE_MODE", "mock")

from app.db.session import init_models, session_scope  # noqa: E402
from app.domain.schemas import CandidateInput, ExceptionCreate  # noqa: E402
from app.engine import director  # noqa: E402
from app.services import timeutil  # noqa: E402
from app.services.mission_service import create_exception_and_mission, get_mission  # noqa: E402
from app.services.view import build_recovery_view  # noqa: E402

BAR = "─" * 78


def payload(pickup: str) -> ExceptionCreate:
    script = {"available": True, "capacity_ok": True, "pickup_time": pickup, "confirmed": True}
    return ExceptionCreate(
        type="carrier_cancellation",
        entity_ref="Shipment #4821",
        description="Carrier A cancelled today's pickup.",
        threatened_outcome="Tonight's shipment departure",
        consequence="Shipment may miss tonight's departure.",
        cutoff_clock="17:30",
        demo=True,
        candidates=[
            CandidateInput(
                name="Carrier B", phone="+15550100001", rank=10, meta={"script": script}
            ),
            CandidateInput(name="Carrier C", phone="+15550100002", rank=20),
            CandidateInput(name="Carrier D", phone="+15550100003", rank=30),
        ],
    )


async def run(pickup: str):
    async with session_scope() as s:
        mission = await create_exception_and_mission(s, payload(pickup))
        mission_id = mission.id
        await director.start_mission(s, mission_id)
    async with session_scope() as s:
        return await build_recovery_view(s, await get_mission(s, mission_id))


def report(label: str, pickup: str, view) -> None:
    print(f"\n{BAR}\n {label}   Carrier B answers: {pickup}   (cutoff 17:30)\n{BAR}")
    for op in view.operations:
        ref = op.calls[0].display_ref if op.calls else "-"
        why = f"  <- {op.reason}" if op.reason else ""
        print(f"  {ref}  ->  {op.target:<12} [{op.status}]{why}")
    for s in view.strategies:
        print(f"  strategy v{s.version}  {s.target:<12} {s.status}")
    print(f"  replans        : {view.mission.replan_count}")
    print(f"  mission status : {view.mission.status}")
    if view.outcome:
        print(f"  outcome        : {view.outcome.headline} via {view.outcome.selected_target}")


async def main() -> int:
    # Pin the clock. The proof compares two runs of the same mission, so it must
    # not also depend on the hour it is run at: past 16:00 the "viable" case
    # would be refused as a collection that can no longer happen, and the
    # comparison would be between two different questions.
    timeutil.set_clock(timeutil.today_at("09:00"))
    await init_models()

    viable = await run("16:00")
    report("CASE A", "16:00", viable)
    late = await run("18:00")
    report("CASE B", "18:00", late)

    a_targets = [o.target for o in viable.operations]
    b_targets = [o.target for o in late.operations]

    checks = {
        "Case A places exactly one call (Carrier B)": a_targets == ["Carrier B"],
        "Case A never calls Carrier C": "Carrier C" not in a_targets,
        "Case A recovers on the first strategy": viable.mission.status == "recovered",
        "Case B invalidates Carrier B": late.strategies[0].status == "invalidated",
        "Case B places a second call to Carrier C": b_targets == ["Carrier B", "Carrier C"],
        "Case B's second call cites the first call's evidence": bool(
            late.operations[1].triggered_by_evidence_id
            and late.latest_replan
            and late.operations[1].triggered_by_evidence_id
            == late.latest_replan.trigger_evidence_id
        ),
        "Case B recovers on the second strategy": late.mission.status == "recovered",
    }

    print(f"\n{BAR}\n VERDICT\n{BAR}")
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}]  {label}")

    ok = all(checks.values())
    print(
        f"\n  Same mission. Same code. Different phone answer. Different behaviour."
        f"\n  => {'NOT SCRIPTED' if ok else 'CHECK FAILED'}\n"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
