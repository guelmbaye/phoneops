"""Mission event bus.

Two responsibilities:
  1. durable audit trail  -> mission_events rows (written by the recorder),
  2. live fan-out         -> in-process asyncio queues + optional Redis pub/sub
     so several API workers can serve the same SSE stream.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from app.config import settings
from app.logging_config import get_logger

log = get_logger("events")

REDIS_CHANNEL = "phoneops:mission-events"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._redis: Any | None = None
        self._reader_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if not settings.REDIS_URL:
            log.info("event_bus.mode", mode="in-process")
            return
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            await self._redis.ping()
            self._reader_task = asyncio.create_task(self._redis_reader())
            log.info("event_bus.mode", mode="redis")
        except Exception as exc:  # pragma: no cover - infra dependent
            self._redis = None
            log.warning("event_bus.redis_unavailable", error=str(exc))

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._redis:
            await self._redis.aclose()

    @property
    def backend(self) -> str:
        return "redis" if self._redis else "in-process"

    # ------------------------------------------------------------- publish
    async def publish(self, mission_id: str, event: dict[str, Any]) -> None:
        payload = {"mission_id": mission_id, "event": event}
        if self._redis:
            try:
                await self._redis.publish(REDIS_CHANNEL, json.dumps(payload, default=str))
                return
            except Exception as exc:  # pragma: no cover
                log.warning("event_bus.publish_failed", error=str(exc))
        await self._dispatch_local(mission_id, event)

    async def _dispatch_local(self, mission_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(mission_id, ())) + list(
                self._subscribers.get("*", ())
            )
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - slow consumer
                log.warning("event_bus.queue_full", mission_id=mission_id)

    async def _redis_reader(self) -> None:  # pragma: no cover - infra dependent
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(REDIS_CHANNEL)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                payload = json.loads(message["data"])
            except json.JSONDecodeError:
                continue
            await self._dispatch_local(payload["mission_id"], payload["event"])

    # ----------------------------------------------------------- subscribe
    async def subscribe(self, mission_id: str) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        async with self._lock:
            self._subscribers[mission_id].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                self._subscribers[mission_id].discard(queue)
                if not self._subscribers[mission_id]:
                    self._subscribers.pop(mission_id, None)


event_bus = EventBus()
