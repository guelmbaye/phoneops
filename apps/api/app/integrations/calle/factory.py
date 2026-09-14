from __future__ import annotations

from app.config import settings
from app.domain.errors import CalleError
from app.integrations.calle.base import CalleClient
from app.integrations.calle.mock_client import MockCalleClient
from app.logging_config import get_logger

log = get_logger("calle.factory")

_client: CalleClient | None = None


def get_calle_client() -> CalleClient:
    """Single process-wide CALL-E client, selected by CALLE_MODE."""
    global _client
    if _client is not None:
        return _client

    mode = settings.CALLE_MODE
    if mode == "http":
        from app.integrations.calle.http_client import HttpCalleClient

        _client = HttpCalleClient()
    elif mode == "cli":
        from app.integrations.calle.cli_client import CliCalleClient

        _client = CliCalleClient()
    elif mode == "mock":
        _client = MockCalleClient()
    else:  # pragma: no cover - guarded by pydantic Literal
        raise CalleError(f"Unknown CALLE_MODE '{mode}'")

    log.info("calle.client.selected", mode=_client.mode)
    return _client


def set_calle_client(client: CalleClient | None) -> None:
    """Test seam."""
    global _client
    _client = client


async def reset_calle_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
