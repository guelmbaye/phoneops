"""5-run reliability gate (DOCUMENT 05 §43). Run before recording the demo."""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./reliability.db")
os.environ.setdefault("DEMO_CALL_LATENCY_SECONDS", "0")

from app.db.session import init_models, session_scope
from app.engine import director
from app.seeds.flagship import build_flagship_payload
from app.services.mission_service import create_exception_and_mission, get_mission
from app.services.view import build_recovery_view

REQUIRED = [
    "CALL_REQUESTED",
    "EVIDENCE_DISCOVERED",
    "CONSTRAINT_VIOLATED",
    "STRATEGY_INVALIDATED",
    "RECOVERY_REPLANNED",
    "RECOVERY_COMPLETED",
]


async def one_run(n: int) -> bool:
    async with session_scope() as s:
        m = await create_exception_and_mission(s, build_flagship_payload())
        mid = m.id
        await director.start_mission(s, mid)
    async with session_scope() as s:
        v = await build_recovery_view(s, await get_mission(s, mid))
    types = [e.type for e in v.timeline]
    checks = {
        "first call": v.operations and v.operations[0].target == "Carrier B",
        "evidence": any(e.subject == "Carrier B" and e.type == "pickup_time" for e in v.evidence),
        "invalidation": "STRATEGY_INVALIDATED" in types,
        "replan": v.mission.replan_count == 1,
        "second call": len(v.operations) == 2 and v.operations[1].target == "Carrier C",
        "causality": v.operations[1].triggered_by_replan_id is not None,
        "outcome": v.outcome and v.outcome.status == "recovered",
        "events": all(t in types for t in REQUIRED),
    }
    ok = all(checks.values())
    print(
        f"RUN {n:02d} {'PASS' if ok else 'FAIL'}  "
        + "  ".join(f"{k}={'ok' if v else 'KO'}" for k, v in checks.items())
    )
    return ok


async def main() -> int:
    await init_models()
    results = [await one_run(i + 1) for i in range(5)]
    print(f"\n{sum(results)}/5 runs passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
