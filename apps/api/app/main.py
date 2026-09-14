"""PHONEOPS AI - Autonomous Exception Recovery for Phone-Dependent Operations.

WHEN THE PLAN BREAKS, PHONEOPS CALLS, LEARNS AND RECOVERS.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes import (
    demo,
    events,
    exceptions,
    health,
    missions,
    operations,
    telnyx_sim,
    webhooks,
)
from app.config import settings
from app.db.session import init_models
from app.domain.errors import PhoneOpsError
from app.engine.director import poll_pending_calls
from app.events.bus import event_bus
from app.integrations.calle import reset_calle_client
from app.logging_config import configure_logging, get_logger

configure_logging()
log = get_logger("app")

DESCRIPTION = """
PHONEOPS recovers time-critical operational exceptions by calling the people who
hold the missing information, turning their answers into structured evidence, and
replanning until it finds a viable recovery path or escalates.

**The call does not complete the workflow. The call changes the recovery plan.**

* `POST /api/exceptions` - exception intake, creates a Recovery Mission
* `POST /api/recovery-missions/{id}/start` - run the adaptive loop
* `GET  /api/recovery-missions/{id}` - Mission Control aggregate
* `GET  /api/recovery-missions/{id}/stream` - live SSE mission events
* `GET  /api/recovery-missions/{id}/explain` - which call caused which replan
* `POST /api/webhooks/calle` - CALL-E terminal result callback
"""


async def _poller() -> None:
    """Fallback for deployments without a public webhook URL."""
    while True:
        try:
            await asyncio.sleep(settings.CALLE_POLL_INTERVAL_SECONDS)
            await poll_pending_calls()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("poller.error", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.AUTO_CREATE_SCHEMA:
        await init_models()
    await event_bus.start()

    poller: asyncio.Task | None = None
    # Poll even when a webhook is configured. The webhook is the fast path, not
    # the only one: a single dropped delivery left a mission stuck on
    # "RECOVERING" with no way back, which on camera is unrecoverable. Ingestion
    # is idempotent, so the two paths cannot double-count.
    if settings.CALLE_MODE != "mock":
        poller = asyncio.create_task(_poller())
        log.info(
            "poller.started",
            interval=settings.CALLE_POLL_INTERVAL_SECONDS,
            role="safety net" if settings.CALLE_WEBHOOK_URL else "primary",
        )

    log.info(
        "app.started",
        env=settings.APP_ENV,
        calle_mode=settings.CALLE_MODE,
        llm_enabled=settings.LLM_ENABLED,
    )
    try:
        yield
    finally:
        if poller:
            poller.cancel()
        await event_bus.stop()
        await reset_calle_client()
        log.info("app.stopped")


app = FastAPI(
    title="PHONEOPS AI",
    version=__version__,
    description=DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PhoneOpsError)
async def phoneops_error_handler(_: Request, exc: PhoneOpsError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


for router in (
    health.router,
    exceptions.router,
    missions.router,
    operations.router,
    events.router,
    webhooks.router,
    demo.router,
):
    app.include_router(router, prefix=settings.API_PREFIX)

# The PSTN test harness answers real phone calls, so it is mounted only when
# explicitly enabled — never by accident, and never in a deployment that has no
# reason to be playing a carrier.
if settings.TELNYX_SIM_ENABLED:
    app.include_router(telnyx_sim.router)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "product": "PHONEOPS AI",
        "category": "Autonomous Exception Recovery for Phone-Dependent Operations",
        "tagline": "When the plan breaks, PHONEOPS calls, learns and recovers.",
        "loop": "CALL -> EVIDENCE -> REPLAN -> CALL AGAIN -> RECOVER",
        "version": __version__,
        "docs": "/docs",
    }
