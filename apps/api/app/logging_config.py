"""Structured logging. Every log line can carry the full causal chain:
mission_id -> strategy_id -> attempt_id -> operation_id -> call_id -> evidence_id.
"""

from __future__ import annotations

import logging
import sys

import structlog

from app.config import settings


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=settings.LOG_LEVEL.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(colors=False)
            if settings.APP_ENV == "local"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "phoneops"):
    return structlog.get_logger(name)
