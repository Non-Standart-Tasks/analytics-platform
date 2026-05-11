"""Async-очередь + воркер для пачковой вставки событий.

Mirror архитектуры telemetry-client'а: HTTP-хендлер кладёт событие в очередь
и сразу возвращает 201, реальная вставка в БД идёт в фоне батчами.
"""

import asyncio
import logging
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.schemas import EventIn

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
BATCH_TIMEOUT_SEC = 1.0

INSERT_SQL = text("""
    INSERT INTO events (
        event_id, type, service, system_env, status, request_id,
        data, payload, client_timestamp
    ) VALUES (
        :event_id, :type, :service, :system_env, :status, :request_id,
        :data, CAST(:payload AS JSONB), :client_timestamp
    )
    ON CONFLICT DO NOTHING
""")


class Ingest:
    def __init__(self, queue_size: int):
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_size)
        self._worker_task: asyncio.Task | None = None

    def enqueue(self, event: EventIn) -> bool:
        """True если поставлено в очередь, False если переполнена."""
        row = {
            "event_id": str(event.event_id),
            "type": event.type,
            "service": event.service,
            "system_env": event.system_env,
            "status": event.status,
            "request_id": event.request_id,
            "data": event.data,
            "payload": event.payload(),
            "client_timestamp": event.client_timestamp,
        }
        try:
            self.queue.put_nowait(row)
            return True
        except asyncio.QueueFull:
            logger.warning("ingest queue full, dropping event type=%s", event.type)
            return False

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._worker(), name="ingest-worker")

    async def stop(self) -> None:
        if self._worker_task:
            await self.queue.join()
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def _worker(self) -> None:
        while True:
            batch = [await self.queue.get()]
            try:
                deadline = asyncio.get_event_loop().time() + BATCH_TIMEOUT_SEC
                while len(batch) < BATCH_SIZE:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break
                    try:
                        batch.append(await asyncio.wait_for(self.queue.get(), timeout=remaining))
                    except asyncio.TimeoutError:
                        break

                await self._flush(batch)
            except Exception:
                logger.exception("ingest worker failed to flush batch of %d", len(batch))
            finally:
                for _ in batch:
                    self.queue.task_done()

    async def _flush(self, batch: list[dict[str, Any]]) -> None:
        async with SessionLocal() as session:
            # asyncpg хочет JSON как строку для CAST в JSONB.
            import json
            for row in batch:
                row["payload"] = json.dumps(row["payload"])
            await session.execute(INSERT_SQL, batch)
            await session.commit()


ingest = Ingest(queue_size=settings.ingest_queue_size)
