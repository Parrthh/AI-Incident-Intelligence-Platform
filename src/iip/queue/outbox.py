"""Transactional outbox (ADR-0008).

The problem: the webhook must store the alert in Postgres *and* queue a job in
Redis. Those are two systems, so there's no transaction across both. Commit
first and publish second, and a crash or Redis outage in between loses the job.
Publish first, and a failed commit leaves a job for data that doesn't exist.

The fix: write the job as a row in Postgres (``outbox``) in the same transaction
as the alert. Either both are committed or neither is. A relay then copies
unpublished rows to Redis and marks them published.

The relay runs in the API process, next to the code that writes the rows:
- ``notify()`` wakes it right after a commit, so jobs normally reach Redis
  within milliseconds.
- It sweeps once at startup, which catches rows left by a process that died
  between commit and publish.
- Otherwise it sweeps only every ``sweep_seconds`` (10 minutes by default).
  Polling Postgres more often would keep the free Neon database from ever
  suspending (ADR-0004).

A row can be published twice (Redis accepted it, then marking it failed), which
is fine: delivery is at least once anyway and handlers are idempotent.
"""

import asyncio
import contextlib
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iip.db.models import OutboxMessage
from iip.queue.streams import publish

logger = structlog.get_logger(__name__)

BATCH_SIZE = 100
RETENTION = timedelta(days=7)
CLEANUP_INTERVAL_SECONDS = 3600.0
MIN_ERROR_PAUSE_SECONDS = 5.0
MAX_ERROR_PAUSE_SECONDS = 300.0


def enqueue(session: AsyncSession, stream: str, job_type: str, payload: dict[str, Any]) -> None:
    """Add a job to the outbox. It's committed (or rolled back) with the caller's transaction."""
    session.add(OutboxMessage(stream=stream, type=job_type, payload=payload))


class OutboxRelay:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        redis: Redis,
        *,
        sweep_seconds: float = 600.0,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._redis = redis
        self._sweep_seconds = sweep_seconds
        self._wake = asyncio.Event()
        self._next_cleanup = 0.0

    def notify(self) -> None:
        """Ask the relay to publish now. Cheap and safe to call from a request."""
        self._wake.set()

    async def publish_pending(self, limit: int = BATCH_SIZE) -> int:
        """Publish up to ``limit`` unpublished rows, oldest first. Returns how many.

        ``FOR UPDATE SKIP LOCKED`` lets several API instances run relays at once:
        each row is locked by one of them, the others skip it instead of waiting.
        On a Redis error, rows published so far are still marked, the failing row
        records the error, and the error is raised.
        """
        async with self._sessionmaker() as session, session.begin():
            rows = (
                await session.scalars(
                    select(OutboxMessage)
                    .where(OutboxMessage.published_at.is_(None))
                    .order_by(OutboxMessage.created_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            published = 0
            failure: Exception | None = None
            for row in rows:
                try:
                    await publish(self._redis, row.stream, row.type, row.payload)
                except Exception as exc:  # noqa: BLE001 - recorded, then re-raised below
                    row.attempts += 1
                    row.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                    failure = exc
                    break
                row.published_at = datetime.now(UTC)
                row.attempts += 1
                published += 1
        if published:
            logger.info("outbox_published", count=published)
        if failure is not None:
            raise failure
        return published

    async def drain(self) -> int:
        """Publish until nothing is left. Returns the total published."""
        total = 0
        while True:
            count = await self.publish_pending()
            total += count
            if count < BATCH_SIZE:
                return total

    async def delete_old(self) -> int:
        cutoff = datetime.now(UTC) - RETENTION
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(
                delete(OutboxMessage)
                .where(OutboxMessage.published_at < cutoff)
                .returning(OutboxMessage.id)
            )
            return len(result.all())

    async def run(self, stop: asyncio.Event) -> None:
        log = logger.bind(component="outbox_relay")
        log.info("outbox_relay_started", sweep_seconds=self._sweep_seconds)
        pause = MIN_ERROR_PAUSE_SECONDS
        timeout: float = 0  # sweep immediately at startup
        try:
            while not stop.is_set():
                await self._wait(stop, timeout)
                if stop.is_set():
                    break
                self._wake.clear()
                try:
                    await self.drain()
                    if time.monotonic() >= self._next_cleanup:
                        self._next_cleanup = time.monotonic() + CLEANUP_INTERVAL_SECONDS
                        if deleted := await self.delete_old():
                            log.info("outbox_cleaned", deleted=deleted)
                    pause = MIN_ERROR_PAUSE_SECONDS
                    timeout = self._sweep_seconds
                except Exception as exc:  # noqa: BLE001 - keep relaying through outages
                    log.warning(
                        "outbox_publish_failed",
                        error_type=type(exc).__name__,
                        error=str(exc)[:300],
                        retry_in_s=pause,
                    )
                    timeout = pause
                    pause = min(MAX_ERROR_PAUSE_SECONDS, pause * 2)
        finally:
            log.info("outbox_relay_stopped")

    async def _wait(self, stop: asyncio.Event, seconds: float) -> None:
        """Wait for a notify, a stop, or the timeout, whichever comes first."""
        waiters = {asyncio.ensure_future(self._wake.wait()), asyncio.ensure_future(stop.wait())}
        try:
            await asyncio.wait(waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter
