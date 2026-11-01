"""Periodic incident housekeeping in the worker.

Every ``interval`` seconds, in one short transaction:
- resolve incidents whose alerts have all resolved and stayed quiet, and
- queue evidence collection for incidents whose debounce has passed.

Then it publishes any queued jobs right away (the outbox relay in the API
process would otherwise pick them up only on its slower fallback sweep).
"""

import asyncio
import contextlib
from datetime import timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iip.enrichment.scheduler import schedule_due_enrichments
from iip.incidents.resolver import resolve_quiet_incidents
from iip.queue.outbox import OutboxRelay

logger = structlog.get_logger(__name__)

DEFAULT_INTERVAL_SECONDS = 10.0


async def sweep_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    quiet: timedelta,
    jobs_stream: str | None,
) -> tuple[int, int]:
    """Returns (incidents resolved, enrichments queued)."""
    async with sessionmaker() as session:
        resolved = await resolve_quiet_incidents(session, quiet)
        queued = await schedule_due_enrichments(session, jobs_stream) if jobs_stream else []
        await session.commit()
    return len(resolved), len(queued)


async def run_sweeper(
    sessionmaker: async_sessionmaker[AsyncSession],
    stop: asyncio.Event,
    *,
    quiet: timedelta,
    jobs_stream: str | None,
    relay: OutboxRelay | None,
    interval: float = DEFAULT_INTERVAL_SECONDS,
) -> None:
    logger.info("sweeper_started", interval=interval, quiet_seconds=quiet.total_seconds())
    try:
        while not stop.is_set():
            try:
                _, queued = await sweep_once(sessionmaker, quiet=quiet, jobs_stream=jobs_stream)
                if queued and relay is not None:
                    await relay.drain()
            except Exception as exc:  # noqa: BLE001 - keep sweeping through blips
                logger.warning("sweep_failed", error_type=type(exc).__name__, error=str(exc)[:300])
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)
    finally:
        logger.info("sweeper_stopped")
