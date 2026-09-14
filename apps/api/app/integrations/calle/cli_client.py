"""CALL-E via the local `calle` CLI / MCP tools.

Fallback path for teams onboarded through the agent install (skills.sh, Codex,
Claude Code, Cursor) rather than the Developer API beta. Uses the documented MCP
tool surface: `plan_call`, `run_call`, `get_call_run`.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Any

from app.config import settings
from app.domain.errors import CalleError
from app.integrations.calle.base import CallRequest, CallState, normalise_outcome
from app.logging_config import get_logger

log = get_logger("calle.cli")


class CliCalleClient:
    mode = "cli"

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or settings.CALLE_CLI_BIN
        if not shutil.which(self.binary):
            raise CalleError(f"`{self.binary}` CLI not found on PATH (CALLE_MODE=cli)")

    async def aclose(self) -> None:  # nothing to close
        return None

    async def create_call(self, request: CallRequest) -> CallState:
        args = {
            "task": request.task,
            "recipient": request.recipient.as_payload(),
            "result_schema": request.result_schema,
            "metadata": request.metadata,
        }
        if request.idempotency_key:
            args["idempotency_key"] = request.idempotency_key
        data = await self._mcp("run_call", args)
        return self._to_state(data)

    async def get_call(self, call_id: str) -> CallState:
        data = await self._mcp("get_call_run", {"call_run_id": call_id})
        return self._to_state(data)

    async def list_events(self, call_id: str) -> list[dict[str, Any]]:
        data = await self._mcp("get_call_run", {"call_run_id": call_id})
        events = data.get("events", [])
        return events if isinstance(events, list) else []

    async def plan_call(self, goal: str) -> dict[str, Any]:
        """Optional pre-flight: let CALL-E draft the call plan before dialing."""
        return await self._mcp("plan_call", {"goal": goal})

    # ------------------------------------------------------------ internals
    async def _mcp(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        env_prefix = [
            "env",
            f"CALLE_SOURCE={settings.CALLE_SOURCE}",
            f"CALLE_INTEGRATION={settings.CALLE_INTEGRATION}",
            f"CALLE_INTEGRATION_VERSION={settings.CALLE_INTEGRATION_VERSION}",
        ]
        cmd = [*env_prefix, self.binary, "mcp", "call", tool, "--json", json.dumps(args)]
        log.info("calle.cli.invoke", tool=tool)

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=settings.CALLE_POLL_TIMEOUT_SECONDS
            )
        except TimeoutError as exc:
            proc.kill()
            raise CalleError(f"`calle mcp call {tool}` timed out", transient=True) from exc

        if proc.returncode != 0:
            raise CalleError(
                f"`calle mcp call {tool}` failed (exit {proc.returncode})",
                transient=True,
                details={"stderr": stderr.decode()[:600]},
            )
        try:
            return json.loads(stdout.decode() or "{}")
        except json.JSONDecodeError as exc:
            raise CalleError(f"`calle mcp call {tool}` returned non-JSON output") from exc

    @staticmethod
    def _to_state(data: dict[str, Any]) -> CallState:
        run = data.get("call_run", data.get("result", data))
        call_id = str(run.get("id") or run.get("call_run_id") or run.get("call_id") or "")
        if not call_id:
            raise CalleError("CALL-E CLI response is missing a call run id")
        status = str(run.get("status") or "queued").lower()
        structured = run.get("structured_result") or run.get("result") or {}
        if not isinstance(structured, dict):
            structured = {}
        return CallState(
            call_id=call_id,
            status=status,
            outcome=normalise_outcome(status, structured),
            structured_result=structured,
            summary=str(run.get("summary") or ""),
            transcript_excerpt=str(run.get("transcript") or "")[:600],
            duration_seconds=run.get("duration_seconds"),
            error=run.get("error"),
            raw=run,
        )
