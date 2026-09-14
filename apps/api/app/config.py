"""Central configuration for the PHONEOPS recovery backend.

Every knob is env-driven so that the same image runs:
  * locally (SQLite + mock CALL-E) for deterministic tests,
  * in demo (Postgres + Redis + real CALL-E) for the Grand Prize run.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

CalleMode = Literal["mock", "http", "cli"]


class Settings(BaseSettings):
    #: Later files win in pydantic-settings, so the repo root is listed first as
    #: the shared baseline and a service-local apps/api/.env overrides it. The
    #: reverse order silently ignores the closest, most specific file.
    #: Real environment variables still beat both.
    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # ---------------------------------------------------------------- runtime
    APP_ENV: str = "local"
    APP_NAME: str = "PHONEOPS AI"
    LOG_LEVEL: str = "INFO"
    API_PREFIX: str = "/api"
    WEB_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --------------------------------------------------------------- storage
    # Async drivers only: postgresql+asyncpg / sqlite+aiosqlite
    DATABASE_URL: str = "sqlite+aiosqlite:///./phoneops.db"
    DB_ECHO: bool = False
    AUTO_CREATE_SCHEMA: bool = True  # dev convenience; Alembic owns prod

    REDIS_URL: str | None = None  # optional: in-process bus is used when unset

    # ---------------------------------------------------------------- CALL-E
    # mock -> deterministic scripted carriers (offline, CI, tests)
    # http -> CALL-E Developer API  (POST /v1/calls, GET /v1/calls/{id})
    # cli  -> local `calle` CLI / MCP tools (plan_call, run_call, get_call_run)
    CALLE_MODE: CalleMode = "mock"
    CALLE_BASE_URL: str = "https://api.heycall-e.com"
    CALLE_API_KEY: str | None = None
    CALLE_CLI_BIN: str = "calle"
    CALLE_SOURCE: str = "phoneops"
    CALLE_INTEGRATION: str = "phoneops_recovery_engine"
    CALLE_INTEGRATION_VERSION: str = "1.0.0"
    CALLE_TIMEOUT_SECONDS: float = 30.0
    CALLE_POLL_INTERVAL_SECONDS: float = 3.0
    CALLE_POLL_TIMEOUT_SECONDS: float = 420.0
    # Public URL of POST {API_PREFIX}/webhooks/calle. When unset we poll instead.
    CALLE_WEBHOOK_URL: str | None = None
    CALLE_WEBHOOK_SECRET: str | None = None
    CALLE_DEFAULT_REGION: str = "US"

    #: The operating timezone a cutoff like "17:30" is expressed in - the
    #: warehouse's wall clock, not the server's. Constraints and phone answers
    #: are wall-clock strings with no offset, so the whole mission has to agree
    #: on one frame or the UI shows a cutoff that contradicts its own
    #: constraint (e.g. "cutoff 18:30" next to "must be before 17:30").
    MISSION_TIMEZONE: str = "UTC"
    CALLE_DEFAULT_LOCALE: str = "en-US"

    # ------------------------------------------------------------------- LLM
    # The recovery loop is fully functional without an LLM: planning,
    # extraction and replanning all have deterministic implementations.
    LLM_ENABLED: bool = False
    LLM_PROVIDER: str = "anthropic"
    LLM_BASE_URL: str = "https://api.anthropic.com"
    LLM_API_KEY: str | None = None
    LLM_MODEL: str = "claude-sonnet-4-6"
    LLM_TIMEOUT_SECONDS: float = 30.0
    LLM_MAX_TOKENS: int = 1200

    # ------------------------------------------------------------ guardrails
    MAX_REPLANS: int = 3
    MAX_CALL_RETRIES: int = 2
    MAX_OPERATIONS_PER_MISSION: int = 8
    MAX_CLARIFICATIONS_PER_TARGET: int = 1
    EVIDENCE_TTL_MINUTES: int = 120

    #: Shortest window in which a phone recovery is worth attempting. Below it,
    #: PHONEOPS would place real outbound calls about an operation it cannot
    #: finish informing before the cutoff. Escalating is the honest answer.
    MIN_RECOVERY_WINDOW_SECONDS: int = 120

    # ---------------------------------------------------------------- policy
    APPROVAL_COST_INCREASE_PCT: float = 15.0  # above this -> human approval
    AUTONOMY_LEVEL: int = 3  # 3 = adaptive (MVP target)

    # ------------------------------------------------------------------ demo
    DEMO_MODE: bool = True
    # Golden path knob. "18:00" violates the 17:30 cutoff -> replan -> Carrier C.
    # "16:00" keeps Carrier B -> no second call. This single value drives the
    # counterfactual proof that the second call is NOT hard-coded.
    # ------------------------------------------------------------- test PSTN
    #: Controlled endpoints that play the demo carriers over a real phone line.
    #: Off by default: this is a test harness, and it answers calls.
    TELNYX_SIM_ENABLED: bool = False
    TELNYX_API_KEY: str = ""
    #: Base64 Ed25519 key from the Telnyx portal. Empty disables verification,
    #: which is acceptable only while wiring the webhook up.
    TELNYX_PUBLIC_KEY: str = ""
    TELNYX_VOICE: str = "female"
    TELNYX_LANGUAGE: str = "en-US"

    DEMO_CARRIER_B_PICKUP: str = "18:00"
    #: Numbers the flagship candidates carry. Read here rather than straight
    #: from os.environ so the seed and the PSTN test endpoints agree on one
    #: source — they were documented in .env.example and read in only one place.
    DEMO_CARRIER_B_PHONE: str = "+15550100001"
    DEMO_CARRIER_C_PHONE: str = "+15550100002"
    DEMO_CARRIER_D_PHONE: str = "+15550100003"
    DEMO_CALL_LATENCY_SECONDS: float = 1.5

    @field_validator("DATABASE_URL")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("sqlite://") and "aiosqlite" not in v:
            return v.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return v

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.WEB_ORIGINS.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
