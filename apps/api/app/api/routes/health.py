from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.api.deps import SessionDep
from app.config import settings
from app.domain.schemas import HealthOut
from app.events.bus import event_bus

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
async def health(session: SessionDep) -> HealthOut:
    try:
        await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # pragma: no cover - infra dependent
        database = f"error: {exc}"

    calle_configured = settings.CALLE_MODE != "http" or bool(settings.CALLE_API_KEY)
    return HealthOut(
        status="ok" if database == "ok" and calle_configured else "degraded",
        app=settings.APP_NAME,
        version=__version__,
        env=settings.APP_ENV,
        calle_mode=settings.CALLE_MODE,
        calle_configured=calle_configured,
        llm_enabled=bool(settings.LLM_ENABLED and settings.LLM_API_KEY),
        database=database,
        event_bus=event_bus.backend,
    )
