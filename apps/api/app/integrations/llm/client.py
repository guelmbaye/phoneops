"""Optional LLM layer.

Boundary rule (DOCUMENT 08 §31-32): the model *proposes*, the system *enforces*.
Model output is JSON-only, schema-validated, and every consumer must have a
deterministic fallback - the recovery loop stays fully functional with
LLM_ENABLED=false, which is also how the counterfactual test runs.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import settings
from app.logging_config import get_logger

log = get_logger("llm")

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


class LLMUnavailable(RuntimeError):
    """Raised whenever the caller must fall back to deterministic logic."""


def llm_enabled() -> bool:
    return bool(settings.LLM_ENABLED and settings.LLM_API_KEY)


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    fenced = _FENCE.search(candidate)
    if fenced:
        candidate = fenced.group(1)
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1:
        raise LLMUnavailable("model did not return a JSON object")
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"model returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMUnavailable("model returned a non-object payload")
    return parsed


def _validate(payload: dict[str, Any], required: list[str]) -> dict[str, Any]:
    missing = [key for key in required if key not in payload]
    if missing:
        raise LLMUnavailable(f"model output missing required keys: {missing}")
    return payload


async def llm_json(
    *, system: str, user: str, required_keys: list[str] | None = None
) -> dict[str, Any]:
    """Ask the model for a JSON object. Raises LLMUnavailable on any problem."""
    if not llm_enabled():
        raise LLMUnavailable("LLM disabled")

    body = {
        "model": settings.LLM_MODEL,
        "max_tokens": settings.LLM_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    headers = {
        "x-api-key": settings.LLM_API_KEY or "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(
            base_url=settings.LLM_BASE_URL, timeout=settings.LLM_TIMEOUT_SECONDS
        ) as client:
            response = await client.post("/v1/messages", json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"transport error: {exc}") from exc

    if response.status_code >= 400:
        raise LLMUnavailable(f"HTTP {response.status_code}: {response.text[:200]}")

    data = response.json()
    text = "".join(
        block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
    )
    return _validate(_extract_json(text), required_keys or [])
