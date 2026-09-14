"""Test harness.

Every test runs against SQLite + the mock CALL-E adapter, with the LLM disabled.
That is deliberate: the recovery loop must be provably deterministic before any
model is added to it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("CALLE_MODE", "mock")
os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("DEMO_CALL_LATENCY_SECONDS", "0")
os.environ.setdefault("APP_ENV", "test")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.domain.schemas import CandidateInput, ExceptionCreate  # noqa: E402
from app.integrations.calle.factory import set_calle_client  # noqa: E402
from app.integrations.calle.mock_client import MockCalleClient  # noqa: E402
from app.services import timeutil  # noqa: E402


@pytest.fixture(autouse=True)
def frozen_clock():
    """Pin "now" so the suite does not depend on the wall clock.

    Without this, every test asserting that a 16:45 pickup satisfies a 17:30
    cutoff starts failing at 16:45 — the suite was green in the morning and red
    in the evening, which is worse than no suite.
    """
    timeutil.set_clock(timeutil.today_at("09:00"))
    yield
    timeutil.set_clock(None)


@pytest_asyncio.fixture
async def engine(tmp_path):
    # File-backed rather than :memory:. SQLAlchemy serves an in-memory SQLite
    # through a StaticPool, so every "independent" session would share one
    # connection - concurrency tests would pass without ever proving isolation,
    # and one session closing would roll back another's open transaction.
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    import app.models  # noqa: F401

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with maker() as s:
        yield s
        await s.commit()


@pytest_asyncio.fixture
async def session_factory(engine):
    """Independent sessions — needed to exercise genuinely concurrent callers."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def calle():
    client = MockCalleClient(latency=0)
    set_calle_client(client)
    yield client
    set_calle_client(None)


@pytest_asyncio.fixture
async def api(engine, calle):
    """ASGI client wired to the test database."""
    from app.db.session import get_session
    from app.main import app

    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    async def _override():
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def flagship_payload(*, carrier_b_script: dict | None = None, **overrides) -> ExceptionCreate:
    """Flagship scenario with a per-candidate scripted phone persona.

    The script models what the human on the phone says. Nothing else changes
    between counterfactual cases.
    """
    candidates = [
        CandidateInput(
            name="Carrier B",
            phone="+15550100001",
            rank=10,
            meta={"script": carrier_b_script} if carrier_b_script else {},
        ),
        CandidateInput(name="Carrier C", phone="+15550100002", rank=20),
        CandidateInput(name="Carrier D", phone="+15550100003", rank=30),
    ]
    data = dict(
        type="carrier_cancellation",
        entity_ref="Shipment #4821",
        description="Carrier A cancelled today's pickup.",
        severity="critical",
        threatened_outcome="Tonight's shipment departure",
        consequence="Shipment may miss tonight's departure.",
        cutoff_clock="17:30",
        candidates=candidates,
        demo=True,
    )
    data.update(overrides)
    return ExceptionCreate(**data)


@pytest.fixture
def make_payload():
    return flagship_payload
